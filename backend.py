"""
backend.py
==========
Business logic for the EV Charging Station Allocation System.

Key ideas
---------
* Each charging station has ONE charger.
* A COUNTING SEMAPHORE holds one permit for every free (available) station.
  - A waiting EV may only start charging after it obtains a permit.
  - When charging ends, the station's permit is released.
* Waiting EVs are served in strict FIFO order (first come, first served).
* One re-entrant lock makes every operation thread-safe.
* A background thread completes finished sessions and starts waiting EVs
  automatically.
* On start-up the whole state is rebuilt from the JSON files, so charger
  states, the waiting queue and the semaphore value are always correct
  after a restart.
"""

import copy
import threading
import traceback
from datetime import datetime, timedelta

import storage

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
# Demo speed: how many REAL seconds equal ONE simulated charging minute.
# 60 = real time, 10 = a 30-minute session takes 5 real minutes.
# It can be changed later from the dashboard.
DEFAULT_SECONDS_PER_MINUTE = 10

# The four stations created automatically the very first time the app runs.
DEFAULT_STATIONS = [
    {"name": "Station Alpha", "location": "Zone A - North Gate", "power_kw": 22.0},
    {"name": "Station Beta", "location": "Zone A - North Gate", "power_kw": 22.0},
    {"name": "Station Gamma", "location": "Zone B - Main Parking", "power_kw": 50.0},
    {"name": "Station Delta", "location": "Zone B - Main Parking", "power_kw": 50.0},
]

# Station statuses
AVAILABLE = "available"
OCCUPIED = "occupied"
MAINTENANCE = "maintenance"

# Request statuses
WAITING = "waiting"
CHARGING = "charging"
COMPLETED = "completed"
CANCELLED = "cancelled"


class BackendError(Exception):
    """Raised for invalid user actions. The message is safe to show in the UI."""


def _fmt(dt: datetime) -> str:
    return dt.strftime(storage.TIME_FORMAT)


def _seq(request: dict) -> int:
    """Numeric part of 'REQ-0007' -> 7. Gives us the FIFO order."""
    return int(request["request_id"].split("-")[-1])


# ===========================================================================
# ChargingSystem
# ===========================================================================
class ChargingSystem:
    def __init__(self, auto_monitor: bool = True):
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._monitor_thread = None
        self._dirty = set()

        self._sem = threading.Semaphore(0)  # rebuilt during recovery
        self._permits = 0                   # mirror of the semaphore value (for display)
        self._settings = {"seconds_per_minute": DEFAULT_SECONDS_PER_MINUTE}

        with self._lock:
            self._load_and_recover()

        if auto_monitor:
            self.start_monitor()

    # -----------------------------------------------------------------------
    # Loading, saving and RECOVERY
    # -----------------------------------------------------------------------
    def _load_and_recover(self) -> None:
        """Read everything from JSON and rebuild a consistent live state."""
        storage.init_storage()
        self._evs = storage.read_data("evs")
        self._stations = storage.read_data("stations")
        self._requests = storage.read_data("requests")
        self._history = storage.read_data("history")

        meta = storage.read_data("meta")
        saved = meta.get("settings", {})
        try:
            spm = int(saved.get("seconds_per_minute", DEFAULT_SECONDS_PER_MINUTE))
        except (TypeError, ValueError):
            spm = DEFAULT_SECONDS_PER_MINUTE
        self._settings = {"seconds_per_minute": min(max(spm, 1), 60)}

        # 1) Create the four default stations on the very first run only
        if not self._stations and not meta.get("stations_seeded"):
            for spec in DEFAULT_STATIONS:
                self._stations.append(
                    {
                        "station_id": storage.next_id("ST"),
                        "name": spec["name"],
                        "location": spec["location"],
                        "power_kw": spec["power_kw"],
                        "status": AVAILABLE,
                        "current_request_id": None,
                        "created_at": storage.now_str(),
                    }
                )
            self._update_meta(stations_seeded=True)

        # 2) Rebuild charger states from the requests that were "charging"
        stations_by_id = {s["station_id"]: s for s in self._stations}
        claimed = set()
        for req in sorted(self._requests, key=_seq):
            if req.get("status") != CHARGING:
                continue
            station = stations_by_id.get(req.get("station_id"))
            valid = (
                station is not None
                and station["station_id"] not in claimed
                and station.get("status") != MAINTENANCE
                and req.get("started_at")
                and req.get("planned_end")
            )
            if valid:
                claimed.add(station["station_id"])
                station["status"] = OCCUPIED
                station["current_request_id"] = req["request_id"]
            else:
                self._requeue(req)  # inconsistent record -> back to the queue

        # 3) Every station that holds no charging EV must be free (or in maintenance)
        for station in self._stations:
            if station["station_id"] not in claimed:
                station["current_request_id"] = None
                if station.get("status") != MAINTENANCE:
                    station["status"] = AVAILABLE

        # 4) Semaphore value = number of free stations
        free = sum(1 for s in self._stations if s["status"] == AVAILABLE)
        self._sem = threading.Semaphore(free)
        self._permits = free

        # 5) Sessions that finished while the app was closed are completed now,
        #    using their ORIGINAL planned end time. Then the queue is served.
        self._process_expired()
        self._dispatch()

        self._mark("evs", "stations", "requests", "history")
        self._flush()

    def _requeue(self, req: dict) -> None:
        req.update(
            status=WAITING,
            station_id=None,
            station_name=None,
            power_kw=None,
            started_at=None,
            planned_end=None,
            planned_seconds=None,
            wait_seconds=None,
        )

    def _mark(self, *keys: str) -> None:
        self._dirty.update(keys)

    def _flush(self) -> None:
        """Write every changed list to its JSON file."""
        sources = {
            "evs": self._evs,
            "stations": self._stations,
            "requests": self._requests,
            "history": self._history,
        }
        for key in list(self._dirty):
            storage.write_data(key, sources[key])
        self._dirty.clear()

    @staticmethod
    def _update_meta(**changes) -> None:
        meta = storage.read_data("meta")
        meta.update(changes)
        storage.write_data("meta", meta)

    # -----------------------------------------------------------------------
    # Semaphore helpers
    # -----------------------------------------------------------------------
    def _try_acquire_permit(self) -> bool:
        """Non-blocking acquire: True if a station is free."""
        if self._sem.acquire(blocking=False):
            self._permits -= 1
            return True
        return False

    def _release_permit(self) -> None:
        self._sem.release()
        self._permits += 1

    # -----------------------------------------------------------------------
    # Small internal lookups
    # -----------------------------------------------------------------------
    def _find_ev(self, ev_id: str):
        return next((e for e in self._evs if e["ev_id"] == ev_id), None)

    def _find_station(self, station_id: str):
        return next((s for s in self._stations if s["station_id"] == station_id), None)

    def _find_request(self, request_id: str):
        return next((r for r in self._requests if r["request_id"] == request_id), None)

    def _active_request_for_ev(self, ev_id: str):
        return next(
            (r for r in self._requests
             if r["ev_id"] == ev_id and r["status"] in (WAITING, CHARGING)),
            None,
        )

    def _waiting_sorted(self) -> list:
        return sorted((r for r in self._requests if r["status"] == WAITING), key=_seq)

    @staticmethod
    def _text(value, label: str, max_len: int = 60) -> str:
        text = str(value or "").strip()
        if not text:
            raise BackendError(f"{label} is required.")
        if len(text) > max_len:
            raise BackendError(f"{label} must be at most {max_len} characters.")
        return text

    @staticmethod
    def _number(value, label: str, low: float, high: float) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError):
            raise BackendError(f"{label} must be a number.")
        if not (low <= number <= high):
            raise BackendError(f"{label} must be between {low:g} and {high:g}.")
        return number

    # -----------------------------------------------------------------------
    # CORE ALGORITHM: allocation and completion
    # -----------------------------------------------------------------------
    def _dispatch(self) -> bool:
        """
        FIFO allocation. While somebody is waiting AND the semaphore gives us a
        permit, assign the oldest waiting EV to a free station.
        Must be called while holding self._lock.
        """
        changed = False
        while True:
            waiting = self._waiting_sorted()
            if not waiting:
                break
            if not self._try_acquire_permit():
                break  # all chargers busy -> everyone keeps waiting
            station = next((s for s in self._stations if s["status"] == AVAILABLE), None)
            if station is None:  # safety net; should never happen
                self._release_permit()
                break
            self._start_charging(waiting[0], station)
            changed = True
        if changed:
            self._mark("requests", "stations")
        return changed

    def _start_charging(self, req: dict, station: dict) -> None:
        now = datetime.now().replace(microsecond=0)
        planned = int(req["duration_minutes"] * self._settings["seconds_per_minute"])
        waited = (now - storage.parse_time(req["requested_at"])).total_seconds()
        req.update(
            status=CHARGING,
            station_id=station["station_id"],
            station_name=station["name"],
            power_kw=station["power_kw"],
            started_at=_fmt(now),
            planned_seconds=planned,
            planned_end=_fmt(now + timedelta(seconds=planned)),
            wait_seconds=max(0, int(waited)),
        )
        station["status"] = OCCUPIED
        station["current_request_id"] = req["request_id"]

    def _process_expired(self) -> bool:
        """Complete every charging session whose planned end time has passed."""
        now = datetime.now()
        changed = False
        charging = [r for r in self._requests if r["status"] == CHARGING]
        charging.sort(key=lambda r: r["planned_end"])
        for req in charging:
            end = storage.parse_time(req["planned_end"])
            if now >= end:
                self._finish(req, end, "completed")
                changed = True
        return changed

    def _finish(self, req: dict, ended: datetime, outcome: str) -> None:
        """Close a charging session, store it in history and free the charger."""
        started = storage.parse_time(req["started_at"])
        planned = max(1, int(req["planned_seconds"]))
        charge_seconds = int((ended - started).total_seconds())
        charge_seconds = min(max(charge_seconds, 0), planned)
        energy = req["power_kw"] * (req["duration_minutes"] / 60) * (charge_seconds / planned)

        req.update(
            status=COMPLETED,
            completed_at=_fmt(ended),
            charge_seconds=charge_seconds,
            energy_kwh=round(energy, 2),
            outcome=outcome,
        )
        self._append_history(req)

        station = self._find_station(req["station_id"])
        if station and station.get("current_request_id") == req["request_id"]:
            station["status"] = AVAILABLE
            station["current_request_id"] = None
            self._release_permit()  # one more free charger

        self._mark("requests", "history", "stations")

    def _append_history(self, req: dict) -> None:
        self._history.append(
            {
                "history_id": storage.next_id("HIS"),
                "request_id": req["request_id"],
                "ev_id": req["ev_id"],
                "plate": req["plate"],
                "owner": req["owner"],
                "model": req.get("model", ""),
                "station_id": req.get("station_id"),
                "station_name": req.get("station_name"),
                "requested_at": req["requested_at"],
                "started_at": req.get("started_at"),
                "ended_at": req.get("completed_at"),
                "duration_minutes": req["duration_minutes"],
                "wait_seconds": req.get("wait_seconds"),
                "charge_seconds": req.get("charge_seconds", 0),
                "energy_kwh": req.get("energy_kwh", 0.0),
                "outcome": req.get("outcome"),
            }
        )

    # -----------------------------------------------------------------------
    # Background monitor
    # -----------------------------------------------------------------------
    def tick(self) -> None:
        """One monitoring step: finish expired sessions, then serve the queue."""
        with self._lock:
            self._process_expired()
            self._dispatch()
            self._flush()

    def _monitor_loop(self) -> None:
        while not self._stop_event.wait(1.0):
            try:
                self.tick()
            except Exception:
                traceback.print_exc()

    def start_monitor(self) -> None:
        if self._monitor_thread and self._monitor_thread.is_alive():
            return
        self._stop_event.clear()
        self._monitor_thread = threading.Thread(
            target=self._monitor_loop, name="ev-monitor", daemon=True
        )
        self._monitor_thread.start()

    def shutdown(self) -> None:
        self._stop_event.set()
        if self._monitor_thread:
            self._monitor_thread.join(timeout=3)

    # -----------------------------------------------------------------------
    # Settings
    # -----------------------------------------------------------------------
    def get_seconds_per_minute(self) -> int:
        with self._lock:
            return self._settings["seconds_per_minute"]

    def set_seconds_per_minute(self, value: int) -> None:
        """Change demo speed for both active sessions and sessions started later."""
        value = int(self._number(value, "Seconds per minute", 1, 60))
        with self._lock:
            now = datetime.now().replace(microsecond=0)

            for req in self._requests:
                if req["status"] != CHARGING:
                    continue
                elapsed = max(
                    0,
                    (now - storage.parse_time(req["started_at"])).total_seconds(),
                )
                old_planned = max(1, req["planned_seconds"])
                progress = min(1.0, elapsed / old_planned)
                new_planned = max(1, int(req["duration_minutes"] * value))
                elapsed_at_new_speed = round(progress * new_planned)

                req["planned_seconds"] = new_planned
                req["started_at"] = _fmt(
                    now - timedelta(seconds=elapsed_at_new_speed)
                )
                req["planned_end"] = _fmt(
                    now + timedelta(seconds=new_planned - elapsed_at_new_speed)
                )

            self._settings["seconds_per_minute"] = value
            self._update_meta(settings={"seconds_per_minute": value})
            if any(req["status"] == CHARGING for req in self._requests):
                self._mark("requests")
                self._flush()

    # -----------------------------------------------------------------------
    # EV management
    # -----------------------------------------------------------------------
    def add_ev(self, owner, plate, model, battery_kwh) -> dict:
        owner = self._text(owner, "Owner name")
        plate = self._text(plate, "Vehicle number", 20).upper()
        model = self._text(model, "Vehicle model")
        battery = self._number(battery_kwh, "Battery capacity (kWh)", 1, 300)
        with self._lock:
            if any(e["plate"].upper() == plate for e in self._evs):
                raise BackendError(f"An EV with vehicle number {plate} already exists.")
            ev = {
                "ev_id": storage.next_id("EV"),
                "owner": owner,
                "plate": plate,
                "model": model,
                "battery_kwh": battery,
                "created_at": storage.now_str(),
            }
            self._evs.append(ev)
            self._mark("evs")
            self._flush()
            return copy.deepcopy(ev)

    def update_ev(self, ev_id, owner, plate, model, battery_kwh) -> dict:
        owner = self._text(owner, "Owner name")
        plate = self._text(plate, "Vehicle number", 20).upper()
        model = self._text(model, "Vehicle model")
        battery = self._number(battery_kwh, "Battery capacity (kWh)", 1, 300)
        with self._lock:
            ev = self._find_ev(ev_id)
            if ev is None:
                raise BackendError("EV not found.")
            if any(e["plate"].upper() == plate and e["ev_id"] != ev_id for e in self._evs):
                raise BackendError(f"Another EV already uses vehicle number {plate}.")
            ev.update(owner=owner, plate=plate, model=model, battery_kwh=battery)
            for req in self._requests:  # keep active requests consistent
                if req["ev_id"] == ev_id and req["status"] in (WAITING, CHARGING):
                    req.update(owner=owner, plate=plate, model=model)
            self._mark("evs", "requests")
            self._flush()
            return copy.deepcopy(ev)

    def delete_ev(self, ev_id) -> None:
        with self._lock:
            ev = self._find_ev(ev_id)
            if ev is None:
                raise BackendError("EV not found.")
            if self._active_request_for_ev(ev_id):
                raise BackendError(
                    "This EV has an active request. Cancel or complete it first."
                )
            self._evs.remove(ev)
            self._mark("evs")
            self._flush()

    def get_evs(self) -> list:
        """All EVs, each with a live 'state' (idle / waiting / charging)."""
        with self._lock:
            rows = []
            for ev in self._evs:
                row = copy.deepcopy(ev)
                active = self._active_request_for_ev(ev["ev_id"])
                row["state"] = active["status"] if active else "idle"
                row["sessions_completed"] = sum(
                    1 for r in self._requests
                    if r["ev_id"] == ev["ev_id"] and r["status"] == COMPLETED
                )
                rows.append(row)
            return rows

    # -----------------------------------------------------------------------
    # Station management
    # -----------------------------------------------------------------------
    def add_station(self, name, location, power_kw) -> dict:
        name = self._text(name, "Station name")
        location = self._text(location, "Location")
        power = self._number(power_kw, "Power (kW)", 1, 350)
        with self._lock:
            if any(s["name"].lower() == name.lower() for s in self._stations):
                raise BackendError(f"A station named '{name}' already exists.")
            station = {
                "station_id": storage.next_id("ST"),
                "name": name,
                "location": location,
                "power_kw": power,
                "status": AVAILABLE,
                "current_request_id": None,
                "created_at": storage.now_str(),
            }
            self._stations.append(station)
            self._release_permit()  # a brand-new free charger = one new permit
            self._mark("stations")
            self._dispatch()        # maybe someone is already waiting
            self._flush()
            return copy.deepcopy(station)

    def update_station(self, station_id, name, location, power_kw) -> dict:
        name = self._text(name, "Station name")
        location = self._text(location, "Location")
        power = self._number(power_kw, "Power (kW)", 1, 350)
        with self._lock:
            station = self._find_station(station_id)
            if station is None:
                raise BackendError("Station not found.")
            if any(s["name"].lower() == name.lower() and s["station_id"] != station_id
                   for s in self._stations):
                raise BackendError(f"A station named '{name}' already exists.")
            station.update(name=name, location=location, power_kw=power)
            # Running sessions keep the power they started with.
            for req in self._requests:
                if req["status"] == CHARGING and req["station_id"] == station_id:
                    req["station_name"] = name
            self._mark("stations", "requests")
            self._flush()
            return copy.deepcopy(station)

    def set_maintenance(self, station_id, enable: bool) -> None:
        """Take a FREE station out of service (maintenance) or put it back."""
        with self._lock:
            station = self._find_station(station_id)
            if station is None:
                raise BackendError("Station not found.")
            if enable:
                if station["status"] == MAINTENANCE:
                    return
                if station["status"] == OCCUPIED:
                    raise BackendError(
                        "This station is charging an EV. Complete the session first."
                    )
                if not self._try_acquire_permit():  # take its permit away
                    raise BackendError("Could not reserve the station. Try again.")
                station["status"] = MAINTENANCE
            else:
                if station["status"] != MAINTENANCE:
                    return
                station["status"] = AVAILABLE
                self._release_permit()  # permit is back
                self._dispatch()
            self._mark("stations")
            self._flush()

    def remove_station(self, station_id) -> None:
        with self._lock:
            station = self._find_station(station_id)
            if station is None:
                raise BackendError("Station not found.")
            if station["status"] == OCCUPIED:
                raise BackendError(
                    "This station is charging an EV. Complete the session first."
                )
            if station["status"] == AVAILABLE:
                if not self._try_acquire_permit():
                    raise BackendError("Could not remove the station. Try again.")
            self._stations.remove(station)
            self._mark("stations")
            self._flush()

    def get_stations(self) -> list:
        """All stations, enriched with live charging information."""
        with self._lock:
            now = datetime.now()
            rows = []
            for station in self._stations:
                row = copy.deepcopy(station)
                row.update(current_plate=None, current_owner=None,
                           progress=0.0, remaining_seconds=0)
                req = self._find_request(station.get("current_request_id") or "")
                if req and req["status"] == CHARGING:
                    elapsed = (now - storage.parse_time(req["started_at"])).total_seconds()
                    planned = max(1, req["planned_seconds"])
                    row["current_plate"] = req["plate"]
                    row["current_owner"] = req["owner"]
                    row["progress"] = round(min(1.0, elapsed / planned), 4)
                    row["remaining_seconds"] = int(max(0, planned - elapsed))
                rows.append(row)
            return rows

    # -----------------------------------------------------------------------
    # Charging requests
    # -----------------------------------------------------------------------
    def submit_request(self, ev_id, duration_minutes) -> dict:
        """
        Create a charging request. It joins the FIFO queue and starts at once
        if a charger is free.
        """
        minutes = int(self._number(duration_minutes, "Charging duration (minutes)", 1, 720))
        with self._lock:
            ev = self._find_ev(ev_id)
            if ev is None:
                raise BackendError("Please select a valid EV.")
            active = self._active_request_for_ev(ev_id)
            if active:
                raise BackendError(
                    f"{ev['plate']} already has an active request "
                    f"({active['request_id']}, {active['status']})."
                )
            req = {
                "request_id": storage.next_id("REQ"),
                "ev_id": ev["ev_id"],
                "plate": ev["plate"],
                "owner": ev["owner"],
                "model": ev["model"],
                "requested_at": storage.now_str(),
                "duration_minutes": minutes,
                "status": WAITING,
                "station_id": None,
                "station_name": None,
                "power_kw": None,
                "started_at": None,
                "planned_end": None,
                "planned_seconds": None,
                "completed_at": None,
                "wait_seconds": None,
                "charge_seconds": None,
                "energy_kwh": None,
                "outcome": None,
            }
            self._requests.append(req)
            self._mark("requests")
            self._dispatch()
            self._flush()
            return copy.deepcopy(req)

    def cancel_request(self, request_id) -> None:
        """Cancel a request that is still WAITING in the queue."""
        with self._lock:
            req = self._find_request(request_id)
            if req is None:
                raise BackendError("Request not found.")
            if req["status"] == CHARGING:
                raise BackendError("This EV is already charging. Use 'Complete charging' instead.")
            if req["status"] != WAITING:
                raise BackendError("Only waiting requests can be cancelled.")
            now = datetime.now()
            req.update(
                status=CANCELLED,
                completed_at=_fmt(now),
                wait_seconds=max(0, int((now - storage.parse_time(req["requested_at"])).total_seconds())),
                charge_seconds=0,
                energy_kwh=0.0,
                outcome="cancelled",
            )
            self._append_history(req)
            self._mark("requests", "history")
            self._flush()

    def complete_request(self, request_id) -> None:
        """Finish a charging session right now (manual completion)."""
        with self._lock:
            req = self._find_request(request_id)
            if req is None:
                raise BackendError("Request not found.")
            if req["status"] != CHARGING:
                raise BackendError("Only an EV that is currently charging can be completed.")
            now = datetime.now()
            planned_end = storage.parse_time(req["planned_end"])
            outcome = "completed" if now >= planned_end else "stopped_early"
            self._finish(req, min(now, planned_end), outcome)
            self._dispatch()  # the freed charger goes to the next EV in the queue
            self._flush()

    def get_requests(self) -> list:
        with self._lock:
            return [copy.deepcopy(r) for r in sorted(self._requests, key=_seq)]

    # -----------------------------------------------------------------------
    # Queue, live sessions, history, summary
    # -----------------------------------------------------------------------
    def _estimate_waits(self, waiting: list) -> list:
        """Estimated seconds until each waiting EV starts (simple simulation)."""
        now = datetime.now()
        free_at = []
        for station in self._stations:
            if station["status"] == MAINTENANCE:
                continue
            if station["status"] == AVAILABLE:
                free_at.append(now)
            else:
                req = self._find_request(station.get("current_request_id") or "")
                end = storage.parse_time(req["planned_end"]) if req else now
                free_at.append(max(end, now))
        if not free_at:
            return [None] * len(waiting)
        spm = self._settings["seconds_per_minute"]
        estimates = []
        for req in waiting:
            i = min(range(len(free_at)), key=lambda k: free_at[k])
            start = free_at[i]
            estimates.append(int((start - now).total_seconds()))
            free_at[i] = start + timedelta(seconds=req["duration_minutes"] * spm)
        return estimates

    def get_waiting_queue(self) -> list:
        """Waiting EVs in FIFO order with position and estimated wait."""
        with self._lock:
            waiting = self._waiting_sorted()
            estimates = self._estimate_waits(waiting)
            rows = []
            for position, (req, est) in enumerate(zip(waiting, estimates), start=1):
                row = copy.deepcopy(req)
                row["position"] = position
                row["estimated_wait_seconds"] = est
                rows.append(row)
            return rows

    def get_active_sessions(self) -> list:
        """Sessions currently charging, with progress and time remaining."""
        with self._lock:
            now = datetime.now()
            rows = []
            for req in self._requests:
                if req["status"] != CHARGING:
                    continue
                row = copy.deepcopy(req)
                elapsed = (now - storage.parse_time(req["started_at"])).total_seconds()
                planned = max(1, req["planned_seconds"])
                row["elapsed_seconds"] = int(min(elapsed, planned))
                row["remaining_seconds"] = int(max(0, planned - elapsed))
                row["progress"] = round(min(1.0, elapsed / planned), 4)
                rows.append(row)
            rows.sort(key=lambda r: r["station_id"])
            return rows

    def get_history(self) -> list:
        """Finished sessions, newest first."""
        with self._lock:
            return [copy.deepcopy(h) for h in reversed(self._history)]

    def get_summary(self) -> dict:
        with self._lock:
            total = len(self._stations)
            occupied = sum(1 for s in self._stations if s["status"] == OCCUPIED)
            maintenance = sum(1 for s in self._stations if s["status"] == MAINTENANCE)
            available = sum(1 for s in self._stations if s["status"] == AVAILABLE)
            in_service = total - maintenance
            done = [h for h in self._history if h["outcome"] in ("completed", "stopped_early")]
            waits = [h["wait_seconds"] for h in done if h.get("wait_seconds") is not None]
            return {
                "total_evs": len(self._evs),
                "total_stations": total,
                "available": available,
                "occupied": occupied,
                "maintenance": maintenance,
                "waiting": sum(1 for r in self._requests if r["status"] == WAITING),
                "charging": sum(1 for r in self._requests if r["status"] == CHARGING),
                "completed": sum(1 for r in self._requests if r["status"] == COMPLETED),
                "cancelled": sum(1 for r in self._requests if r["status"] == CANCELLED),
                "total_requests": len(self._requests),
                "total_energy_kwh": round(sum(h.get("energy_kwh") or 0 for h in done), 2),
                "avg_wait_seconds": round(sum(waits) / len(waits), 1) if waits else 0.0,
                "utilization_pct": round(100 * occupied / in_service, 1) if in_service else 0.0,
                "semaphore_permits": self._permits,
                "seconds_per_minute": self._settings["seconds_per_minute"],
            }

    # -----------------------------------------------------------------------
    # Reset
    # -----------------------------------------------------------------------
    def reset_system(self) -> None:
        """Erase all data, recreate the four default stations, keep the demo speed."""
        with self._lock:
            speed = self._settings["seconds_per_minute"]
            storage.reset_all_data()
            self._update_meta(settings={"seconds_per_minute": speed})
            self._load_and_recover()


# ===========================================================================
# Self-test:  python backend.py
# Uses a TEMPORARY folder, so your real data/ files are never touched.
# ===========================================================================
def _self_test() -> None:
    import tempfile
    import time
    from pathlib import Path

    tmp = Path(tempfile.mkdtemp(prefix="ev_test_"))
    storage.DATA_DIR = tmp
    storage.FILES = {key: tmp / path.name for key, path in storage.FILES.items()}
    print(f"Testing in temporary folder: {tmp}\n")

    system = ChargingSystem(auto_monitor=False)
    system.set_seconds_per_minute(1)  # a "1 minute" session lasts 1 second

    assert len(system.get_stations()) == 4, "Should start with 4 stations"
    print("1. Four stations created ........................ OK")

    evs = [system.add_ev(f"Owner {i}", f"TS-{i:02d}", "Test EV", 60) for i in range(1, 7)]
    for ev in evs:
        system.submit_request(ev["ev_id"], 1)
    summary = system.get_summary()
    assert summary["charging"] == 4 and summary["waiting"] == 2, summary
    assert summary["semaphore_permits"] == 0, summary
    print("2. 6 requests -> 4 charging, 2 waiting, permits=0  OK")

    queue = system.get_waiting_queue()
    assert [q["plate"] for q in queue] == ["TS-05", "TS-06"], "FIFO order broken"
    print("3. FIFO queue order ............................. OK")

    time.sleep(1.3)
    system.tick()
    summary = system.get_summary()
    assert summary["completed"] == 4 and summary["charging"] == 2, summary
    assert summary["waiting"] == 0, summary
    print("4. Auto completion + auto allocation ............ OK")

    time.sleep(1.3)
    system.tick()
    system.set_seconds_per_minute(60)
    system.submit_request(evs[0]["ev_id"], 30)  # long session (30 real minutes)
    time.sleep(1.3)
    system.set_seconds_per_minute(1)            # active session adopts the new speed
    assert system.get_active_sessions()[0]["planned_seconds"] == 30
    print("5. Demo speed updates active sessions .......... OK")

    system.shutdown()
    restarted = ChargingSystem(auto_monitor=False)  # simulate an app restart
    summary = restarted.get_summary()
    assert summary["completed"] == 6, summary
    assert summary["occupied"] == 1 and summary["charging"] == 1, summary
    assert summary["semaphore_permits"] == 3, summary
    session = restarted.get_active_sessions()[0]
    assert session["plate"] == "TS-01", session
    assert session["planned_seconds"] == 30, session
    print("6. Restart recovery (charger + semaphore) ....... OK")

    restarted.shutdown()
    print("\nALL TESTS PASSED")


if __name__ == "__main__":
    _self_test()