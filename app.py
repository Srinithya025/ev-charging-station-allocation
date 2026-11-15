"""
app.py
======
Streamlit dashboard for the EV Charging Station Allocation System.

Run with:   streamlit run app.py

Pages: Overview, EV Management, Charging Station Management, Charging Requests,
       Waiting Queue, Live Monitoring, Charging History, Analytics
"""

import html
import inspect
from datetime import datetime

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from backend import BackendError, ChargingSystem

# ---------------------------------------------------------------------------
# Page configuration (must be the first Streamlit call)
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="EV Charging Station Allocation System",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# Styling (blue and white)
# ---------------------------------------------------------------------------
CSS = """
<style>
#MainMenu {visibility: hidden;}
footer {visibility: hidden;}
.block-container {padding-top: 1.5rem; padding-bottom: 3rem; max-width: 1400px;}

/* Sidebar */
[data-testid="stSidebar"] {background: linear-gradient(180deg, #0b2a5b 0%, #1d4ed8 100%);}
[data-testid="stSidebar"] * {color: #ffffff;}
[data-testid="stSidebar"] [role="radiogroup"] label {
    padding: 6px 10px; border-radius: 8px; width: 100%;
}
[data-testid="stSidebar"] [role="radiogroup"] label:hover {background: rgba(255,255,255,0.15);}
[data-testid="stSidebar"] .stButton > button {background: #ffffff; border: none; font-weight: 600;}
[data-testid="stSidebar"] .stButton > button * {color: #0b2a5b !important;}
[data-testid="stSidebar"] hr {border-color: rgba(255,255,255,0.25);}
.brand {font-size: 1.25rem; font-weight: 800; line-height: 1.25; margin-bottom: 0.2rem;}
.brand-sub {font-size: 0.8rem; opacity: 0.85; margin-bottom: 0.8rem;}

/* Page header banner */
.hero {
    background: linear-gradient(120deg, #0b2a5b 0%, #2563eb 100%);
    padding: 1.3rem 1.8rem; border-radius: 16px; margin-bottom: 1.2rem;
    box-shadow: 0 4px 14px rgba(29, 78, 216, 0.25);
}
.hero h1 {color: #ffffff !important; margin: 0; font-size: 1.7rem; padding: 0;}
.hero p {color: #dbeafe !important; margin: 0.25rem 0 0 0; font-size: 0.95rem;}

/* KPI cards */
.kpi {
    background: #ffffff; border-radius: 14px; padding: 1rem 1.2rem;
    border-left: 6px solid #2563eb; box-shadow: 0 2px 10px rgba(11, 42, 91, 0.08);
}
.kpi-label {font-size: 0.78rem; text-transform: uppercase; letter-spacing: 0.05em; color: #5b7bb0;}
.kpi-value {font-size: 1.9rem; font-weight: 800; color: #0b2a5b; line-height: 1.2;}
.kpi-note {font-size: 0.78rem; color: #6b86b8;}

/* Station cards */
.scard {
    background: #ffffff; border-radius: 14px; padding: 1rem 1.1rem; margin-bottom: 1rem;
    box-shadow: 0 2px 10px rgba(11, 42, 91, 0.08); border-top: 5px solid #2563eb;
}
.scard-name {font-weight: 800; font-size: 1.05rem; color: #0b2a5b;}
.scard-meta {font-size: 0.8rem; color: #6b86b8; margin-bottom: 0.5rem;}
.muted {font-size: 0.85rem; color: #35568f;}
.badge {display: inline-block; padding: 2px 10px; border-radius: 999px; font-size: 0.75rem; font-weight: 700;}
.bar {background: #dbeafe; border-radius: 999px; height: 10px; margin: 8px 0 4px 0; overflow: hidden;}
.bar-fill {background: linear-gradient(90deg, #2563eb, #60a5fa); height: 10px; border-radius: 999px;}

/* Semaphore tokens */
.toks {display: flex; gap: 12px; flex-wrap: wrap; margin: 0.4rem 0 0.2rem 0;}
.tok {
    width: 110px; text-align: center; padding: 10px 6px; border-radius: 12px;
    font-size: 0.8rem; color: #0b2a5b; border: 2px solid transparent;
}
.tok-free {background: #dcfce7; border-color: #16a34a;}
.tok-busy {background: #dbeafe; border-color: #1d4ed8;}
.tok-maint {background: #fef3c7; border-color: #d97706;}
</style>
"""
st.markdown(CSS, unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# The backend (created ONCE and shared by every rerun of this script)
# ---------------------------------------------------------------------------
@st.cache_resource
def get_system() -> ChargingSystem:
    return ChargingSystem()


system = get_system()


# ---------------------------------------------------------------------------
# Compatibility helpers (work on old and new Streamlit versions)
# ---------------------------------------------------------------------------
def _stretch_kwargs(func) -> dict:
    params = inspect.signature(func).parameters
    if "use_container_width" in params:
        return {"use_container_width": True}
    return {"width": "stretch"}


_TABLE_KW = _stretch_kwargs(st.dataframe)
_CHART_KW = _stretch_kwargs(st.plotly_chart)


def show_table(df: pd.DataFrame, height: int = None) -> None:
    kwargs = dict(_TABLE_KW, hide_index=True)
    if height:
        kwargs["height"] = height
    st.dataframe(df, **kwargs)


def show_chart(fig) -> None:
    st.plotly_chart(fig, **_CHART_KW)


def auto_refresh(seconds: int):
    """Decorator: re-run just this part of the page every N seconds."""
    if hasattr(st, "fragment"):
        return st.fragment(run_every=seconds)
    return lambda func: func


# ---------------------------------------------------------------------------
# UI helpers
# ---------------------------------------------------------------------------
def esc(value) -> str:
    return html.escape(str(value if value is not None else ""))


def fmt_secs(sec) -> str:
    if sec is None:
        return "-"
    sec = int(sec)
    minutes, seconds = divmod(sec, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes:02d}m {seconds:02d}s"
    if minutes:
        return f"{minutes}m {seconds:02d}s"
    return f"{seconds}s"


def page_header(title: str, subtitle: str) -> None:
    st.markdown(
        f'<div class="hero"><h1>{esc(title)}</h1><p>{esc(subtitle)}</p></div>',
        unsafe_allow_html=True,
    )


def kpi(col, label, value, note="") -> None:
    col.markdown(
        f'<div class="kpi"><div class="kpi-label">{esc(label)}</div>'
        f'<div class="kpi-value">{esc(value)}</div>'
        f'<div class="kpi-note">{esc(note)}</div></div>',
        unsafe_allow_html=True,
    )


def flash(kind: str, message: str) -> None:
    """Remember a message and show it after the next page reload."""
    st.session_state["_flash"] = (kind, message)


def show_flash() -> None:
    item = st.session_state.pop("_flash", None)
    if item:
        kind, message = item
        {"success": st.success, "error": st.error,
         "warning": st.warning, "info": st.info}.get(kind, st.info)(message)


def fkey(name: str) -> str:
    """Form key that changes after a successful submit (this clears the form)."""
    return f"{name}_{st.session_state.get('_n_' + name, 0)}"


def bump(name: str) -> None:
    st.session_state["_n_" + name] = st.session_state.get("_n_" + name, 0) + 1


def attempt(func, *args, success="Done.", reset=None) -> None:
    """Run a backend action. Show an error, or flash success and reload."""
    try:
        result = func(*args)
    except BackendError as exc:
        st.error(str(exc))
        return
    flash("success", success(result) if callable(success) else success)
    if reset:
        bump(reset)
    st.rerun()


STATUS_STYLE = {
    "available": ("Available", "#166534", "#dcfce7", "#16a34a"),
    "occupied": ("Charging", "#1e3a8a", "#dbeafe", "#1d4ed8"),
    "maintenance": ("Maintenance", "#92400e", "#fef3c7", "#d97706"),
}

OUTCOME_LABEL = {
    "completed": "Completed",
    "stopped_early": "Stopped early",
    "cancelled": "Cancelled",
}


def station_card_html(s: dict) -> str:
    label, fg, bg, accent = STATUS_STYLE.get(s["status"], STATUS_STYLE["available"])
    if s["status"] == "occupied":
        pct = int(s["progress"] * 100)
        body = (
            f'<div class="muted"><b>{esc(s["current_plate"])}</b> · {esc(s["current_owner"])}</div>'
            f'<div class="bar"><div class="bar-fill" style="width:{pct}%"></div></div>'
            f'<div class="muted">{pct}% · {fmt_secs(s["remaining_seconds"])} left</div>'
        )
    elif s["status"] == "maintenance":
        body = '<div class="muted">Out of service</div>'
    else:
        body = '<div class="muted">Ready for the next EV</div>'
    return (
        f'<div class="scard" style="border-top-color:{accent}">'
        f'<div class="scard-name">{esc(s["name"])}</div>'
        f'<div class="scard-meta">{esc(s["location"])} · {s["power_kw"]:g} kW</div>'
        f'<span class="badge" style="color:{fg};background:{bg}">{label}</span>'
        f'<div style="margin-top:0.6rem">{body}</div></div>'
    )


def render_station_grid(stations: list, per_row: int = 4) -> None:
    if not stations:
        st.info("No charging stations yet. Add one in Charging Station Management.")
        return
    for i in range(0, len(stations), per_row):
        cols = st.columns(per_row)
        for col, station in zip(cols, stations[i:i + per_row]):
            col.markdown(station_card_html(station), unsafe_allow_html=True)


def permit_tokens_html(stations: list) -> str:
    boxes = []
    for s in stations:
        if s["status"] == "available":
            css, text = "tok-free", "FREE PERMIT"
        elif s["status"] == "occupied":
            css, text = "tok-busy", "IN USE"
        else:
            css, text = "tok-maint", "MAINTENANCE"
        boxes.append(f'<div class="tok {css}"><b>{esc(s["name"])}</b><br>{text}</div>')
    return '<div class="toks">' + "".join(boxes) + "</div>"


BLUES = ["#1d4ed8", "#60a5fa", "#0b2a5b", "#93c5fd", "#3b82f6", "#bfdbfe"]


def style_fig(fig, height: int = 340):
    fig.update_layout(
        template="plotly_white",
        height=height,
        margin=dict(l=10, r=10, t=55, b=10),
        font=dict(color="#0b2a5b"),
        title_font=dict(size=16),
        paper_bgcolor="#ffffff",
        plot_bgcolor="#ffffff",
        legend_title_text="",
    )
    return fig


# ===========================================================================
# PAGE 1: Overview
# ===========================================================================
@auto_refresh(2)
def overview_live() -> None:
    s = system.get_summary()
    row = st.columns(4)
    kpi(row[0], "Total stations", s["total_stations"], f"{s['maintenance']} in maintenance")
    kpi(row[1], "Free chargers", s["available"], "permits in the semaphore")
    kpi(row[2], "Charging now", s["charging"], f"{s['utilization_pct']}% utilization")
    kpi(row[3], "Waiting in queue", s["waiting"], f"avg wait {fmt_secs(s['avg_wait_seconds'])}")
    st.write("")
    row = st.columns(4)
    kpi(row[0], "Registered EVs", s["total_evs"], "")
    kpi(row[1], "Total requests", s["total_requests"], f"{s['cancelled']} cancelled")
    kpi(row[2], "Completed sessions", s["completed"], "")
    kpi(row[3], "Energy delivered", f"{s['total_energy_kwh']} kWh", "completed sessions")
    st.markdown("### Charging stations")
    render_station_grid(system.get_stations())
    st.caption(f"Auto-refreshing every 2 seconds · last update {datetime.now():%H:%M:%S}")


def page_overview() -> None:
    page_header(
        "EV Charging Station Allocation System",
        "Fair, FIFO-based allocation of limited chargers using a counting semaphore.",
    )
    show_flash()
    overview_live()
    st.markdown("### How it works")
    st.info(
        "Every free charger is one permit in a **counting semaphore**. "
        "When an EV requests charging it takes a permit and starts immediately. "
        "If no permit is free, it joins the **FIFO waiting queue**. "
        "When a charger is released, the EV that has waited longest is assigned automatically."
    )


# ===========================================================================
# PAGE 2: EV Management
# ===========================================================================
def page_evs() -> None:
    page_header("EV Management", "Register, edit and remove electric vehicles.")
    show_flash()
    tab_add, tab_list, tab_edit = st.tabs(["Register EV", "All EVs", "Edit / Delete"])

    with tab_add:
        fk = fkey("add_ev")
        with st.form(fk):
            c1, c2 = st.columns(2)
            owner = c1.text_input("Owner name", key=f"{fk}_owner")
            plate = c2.text_input("Vehicle number", placeholder="e.g. TS09AB1234", key=f"{fk}_plate")
            model = c1.text_input("Vehicle model", placeholder="e.g. Tata Nexon EV", key=f"{fk}_model")
            battery = c2.number_input(
                "Battery capacity (kWh)", min_value=1.0, max_value=300.0,
                value=40.0, step=1.0, key=f"{fk}_battery",
            )
            submitted = st.form_submit_button("Register EV", type="primary")
        if submitted:
            attempt(
                system.add_ev, owner, plate, model, battery,
                success=f"EV {str(plate).strip().upper()} registered successfully.",
                reset="add_ev",
            )

    with tab_list:
        evs = system.get_evs()
        if not evs:
            st.info("No EVs registered yet. Use the 'Register EV' tab.")
        else:
            search = st.text_input("Search by owner, vehicle number or model", key="ev_search")
            df = pd.DataFrame(evs)
            if search:
                needle = search.lower()
                mask = (
                    df["owner"].str.lower().str.contains(needle)
                    | df["plate"].str.lower().str.contains(needle)
                    | df["model"].str.lower().str.contains(needle)
                )
                df = df[mask]
            table = pd.DataFrame({
                "EV ID": df["ev_id"],
                "Owner": df["owner"],
                "Vehicle No.": df["plate"],
                "Model": df["model"],
                "Battery (kWh)": df["battery_kwh"],
                "State": df["state"].str.capitalize(),
                "Sessions done": df["sessions_completed"],
                "Registered": df["created_at"],
            })
            show_table(table)
            st.caption(f"{len(table)} EV(s) shown")

    with tab_edit:
        evs = system.get_evs()
        if not evs:
            st.info("Nothing to edit yet.")
        else:
            ev_map = {e["ev_id"]: e for e in evs}
            ev_id = st.selectbox(
                "Select an EV", list(ev_map),
                format_func=lambda i: f"{i} · {ev_map[i]['plate']} · {ev_map[i]['owner']}",
                key="edit_ev_select",
            )
            ev = ev_map[ev_id]
            with st.form(f"edit_ev_form_{ev_id}"):
                c1, c2 = st.columns(2)
                owner = c1.text_input("Owner name", value=ev["owner"], key=f"eo_{ev_id}")
                plate = c2.text_input("Vehicle number", value=ev["plate"], key=f"ep_{ev_id}")
                model = c1.text_input("Vehicle model", value=ev["model"], key=f"em_{ev_id}")
                battery = c2.number_input(
                    "Battery capacity (kWh)", min_value=1.0, max_value=300.0,
                    value=float(ev["battery_kwh"]), step=1.0, key=f"eb_{ev_id}",
                )
                saved = st.form_submit_button("Save changes", type="primary")
            if saved:
                attempt(system.update_ev, ev_id, owner, plate, model, battery,
                        success="EV updated.")

            st.markdown("#### Delete this EV")
            st.caption("An EV with a waiting or charging request cannot be deleted.")
            confirm = st.checkbox("Yes, delete this EV permanently", key=f"del_ev_ok_{ev_id}")
            if st.button("Delete EV", disabled=not confirm, key=f"del_ev_btn_{ev_id}"):
                attempt(system.delete_ev, ev_id, success=f"EV {ev['plate']} deleted.")


# ===========================================================================
# PAGE 3: Charging Station Management
# ===========================================================================
def page_stations() -> None:
    page_header("Charging Station Management", "Add chargers, edit them, or take them out of service.")
    show_flash()
    tab_list, tab_add, tab_manage = st.tabs(["All Stations", "Add Station", "Edit / Maintenance / Remove"])

    with tab_list:
        stations = system.get_stations()
        if not stations:
            st.info("No stations configured.")
        else:
            render_station_grid(stations)
            table = pd.DataFrame({
                "Station ID": [s["station_id"] for s in stations],
                "Name": [s["name"] for s in stations],
                "Location": [s["location"] for s in stations],
                "Power (kW)": [s["power_kw"] for s in stations],
                "Status": [STATUS_STYLE[s["status"]][0] for s in stations],
                "Current EV": [s["current_plate"] or "-" for s in stations],
                "Progress (%)": [int(s["progress"] * 100) for s in stations],
            })
            show_table(table)

    with tab_add:
        fk = fkey("add_station")
        with st.form(fk):
            c1, c2, c3 = st.columns(3)
            name = c1.text_input("Station name", placeholder="e.g. Station Epsilon", key=f"{fk}_name")
            location = c2.text_input("Location", placeholder="e.g. Zone C - East Gate", key=f"{fk}_loc")
            power = c3.number_input("Power (kW)", min_value=1.0, max_value=350.0,
                                    value=22.0, step=1.0, key=f"{fk}_pow")
            submitted = st.form_submit_button("Add station", type="primary")
        st.caption("A new station adds one permit to the semaphore. Waiting EVs are assigned to it immediately.")
        if submitted:
            attempt(system.add_station, name, location, power,
                    success=f"Station '{str(name).strip()}' added.", reset="add_station")

    with tab_manage:
        stations = system.get_stations()
        if not stations:
            st.info("No stations to manage.")
            return
        smap = {s["station_id"]: s for s in stations}
        sid = st.selectbox(
            "Select a station", list(smap),
            format_func=lambda i: f"{i} · {smap[i]['name']} ({STATUS_STYLE[smap[i]['status']][0]})",
            key="manage_station_select",
        )
        station = smap[sid]

        with st.form(f"edit_station_form_{sid}"):
            c1, c2, c3 = st.columns(3)
            name = c1.text_input("Station name", value=station["name"], key=f"sn_{sid}")
            location = c2.text_input("Location", value=station["location"], key=f"sl_{sid}")
            power = c3.number_input("Power (kW)", min_value=1.0, max_value=350.0,
                                    value=float(station["power_kw"]), step=1.0, key=f"sp_{sid}")
            saved = st.form_submit_button("Save changes", type="primary")
        if saved:
            attempt(system.update_station, sid, name, location, power, success="Station updated.")

        st.markdown("#### Maintenance mode")
        st.caption("Only a free station can enter maintenance. Its permit is removed from the semaphore.")
        if station["status"] == "maintenance":
            if st.button("Return to service", type="primary", key=f"svc_{sid}"):
                attempt(system.set_maintenance, sid, False,
                        success=f"{station['name']} is back in service.")
        else:
            if st.button("Put in maintenance", key=f"mnt_{sid}"):
                attempt(system.set_maintenance, sid, True,
                        success=f"{station['name']} is now in maintenance.")

        st.markdown("#### Remove station")
        st.caption("A station that is charging an EV cannot be removed.")
        confirm = st.checkbox("Yes, remove this station permanently", key=f"rm_ok_{sid}")
        if st.button("Remove station", disabled=not confirm, key=f"rm_btn_{sid}"):
            attempt(system.remove_station, sid, success=f"Station '{station['name']}' removed.")


# ===========================================================================
# PAGE 4: Charging Requests
# ===========================================================================
def page_requests() -> None:
    page_header("Charging Requests", "Submit requests, finish sessions and review every request.")
    show_flash()

    st.markdown("### New charging request")
    all_evs = system.get_evs()
    idle = [e for e in all_evs if e["state"] == "idle"]
    if not all_evs:
        st.info("Register an EV first (EV Management).")
    elif not idle:
        st.info("Every registered EV already has a waiting or charging request.")
    else:
        ev_map = {e["ev_id"]: e for e in idle}
        fk = fkey("new_request")
        with st.form(fk):
            c1, c2 = st.columns([2, 1])
            ev_id = c1.selectbox(
                "Electric vehicle", list(ev_map),
                format_func=lambda i: f"{i} · {ev_map[i]['plate']} · {ev_map[i]['owner']} ({ev_map[i]['model']})",
                key=f"{fk}_ev",
            )
            minutes = c2.number_input("Charging duration (minutes)", min_value=1, max_value=720,
                                      value=30, step=5, key=f"{fk}_min")
            submitted = st.form_submit_button("Request charging", type="primary")
        st.caption(
            f"Demo speed: 1 charging minute = {system.get_seconds_per_minute()} real second(s). "
            "Change it in the sidebar."
        )
        if submitted:
            def message(req):
                if req["status"] == "charging":
                    return f"{req['plate']} started charging at {req['station_name']}."
                return f"No charger is free. {req['plate']} was added to the waiting queue."
            attempt(system.submit_request, ev_id, int(minutes), success=message, reset="new_request")

    st.markdown("### Manage active requests")
    col_a, col_b = st.columns(2)

    with col_a:
        st.markdown("#### Complete charging")
        active = system.get_active_sessions()
        if not active:
            st.info("No EV is charging right now.")
        else:
            amap = {a["request_id"]: a for a in active}
            rid = st.selectbox(
                "Charging session", list(amap),
                format_func=lambda r: f"{r} · {amap[r]['plate']} at {amap[r]['station_name']}",
                key="complete_select",
            )
            st.caption("Completing early records only the energy delivered so far.")
            if st.button("Complete charging now", type="primary", key="complete_btn"):
                attempt(system.complete_request, rid,
                        success="Charging finished. The charger was released and the next waiting EV was assigned.")

    with col_b:
        st.markdown("#### Cancel a waiting request")
        queue = system.get_waiting_queue()
        if not queue:
            st.info("Nobody is waiting.")
        else:
            qmap = {q["request_id"]: q for q in queue}
            rid = st.selectbox(
                "Waiting request", list(qmap),
                format_func=lambda r: f"#{qmap[r]['position']} · {r} · {qmap[r]['plate']}",
                key="cancel_select_req",
            )
            if st.button("Cancel request", key="cancel_btn_req"):
                attempt(system.cancel_request, rid, success="Request cancelled.")

    st.markdown("### All requests")
    requests = system.get_requests()
    if not requests:
        st.info("No requests yet.")
        return
    options = ["waiting", "charging", "completed", "cancelled"]
    chosen = st.multiselect("Filter by status", options, default=options, key="req_status_filter")
    df = pd.DataFrame(requests)
    df = df[df["status"].isin(chosen)].iloc[::-1]
    table = pd.DataFrame({
        "Request": df["request_id"],
        "Vehicle No.": df["plate"],
        "Owner": df["owner"],
        "Station": df["station_name"].fillna("-"),
        "Requested": df["requested_at"],
        "Duration (min)": df["duration_minutes"],
        "Status": df["status"].str.capitalize(),
        "Waited (s)": df["wait_seconds"],
    })
    show_table(table)


# ===========================================================================
# PAGE 5: Waiting Queue
# ===========================================================================
@auto_refresh(2)
def queue_live() -> None:
    queue = system.get_waiting_queue()
    s = system.get_summary()
    row = st.columns(3)
    kpi(row[0], "EVs waiting", len(queue), "in FIFO order")
    kpi(row[1], "Next in line", queue[0]["plate"] if queue else "-", "served first")
    longest = max((q["estimated_wait_seconds"] or 0 for q in queue), default=0)
    kpi(row[2], "Longest estimated wait", fmt_secs(longest) if queue else "-",
        f"{s['available']} charger(s) free now")
    st.write("")
    if not queue:
        st.success("The queue is empty. Every request is being served.")
    else:
        table = pd.DataFrame({
            "Position": [q["position"] for q in queue],
            "Request": [q["request_id"] for q in queue],
            "Vehicle No.": [q["plate"] for q in queue],
            "Owner": [q["owner"] for q in queue],
            "Model": [q["model"] for q in queue],
            "Requested at": [q["requested_at"] for q in queue],
            "Duration (min)": [q["duration_minutes"] for q in queue],
            "Estimated wait": [
                fmt_secs(q["estimated_wait_seconds"]) if q["estimated_wait_seconds"] is not None
                else "No active charger"
                for q in queue
            ],
        })
        show_table(table)
    st.caption(f"Auto-refreshing every 2 seconds · last update {datetime.now():%H:%M:%S}")


def page_queue() -> None:
    page_header("Waiting Queue", "First come, first served. Position 1 gets the next free charger.")
    show_flash()
    queue_live()
    st.markdown("### Cancel a waiting request")
    queue = system.get_waiting_queue()
    if not queue:
        st.caption("Nothing to cancel.")
        return
    qmap = {q["request_id"]: q for q in queue}
    rid = st.selectbox(
        "Waiting request", list(qmap),
        format_func=lambda r: f"#{qmap[r]['position']} · {r} · {qmap[r]['plate']} · {qmap[r]['owner']}",
        key="cancel_select_queue",
    )
    if st.button("Cancel request", key="cancel_btn_queue"):
        attempt(system.cancel_request, rid, success="Request cancelled and removed from the queue.")


# ===========================================================================
# PAGE 6: Live Monitoring
# ===========================================================================
@auto_refresh(2)
def monitor_live() -> None:
    summary = system.get_summary()
    stations = system.get_stations()
    sessions = system.get_active_sessions()

    row = st.columns(4)
    kpi(row[0], "Semaphore permits", summary["semaphore_permits"], "free chargers right now")
    kpi(row[1], "Chargers busy", summary["occupied"], f"of {summary['total_stations']} stations")
    kpi(row[2], "Waiting EVs", summary["waiting"], "in the FIFO queue")
    kpi(row[3], "Utilization", f"{summary['utilization_pct']}%", "of in-service chargers")

    st.markdown("### Counting semaphore")
    st.markdown(permit_tokens_html(stations), unsafe_allow_html=True)
    st.caption("A green token is one free permit. An EV needs a permit to start charging.")

    st.markdown("### Stations")
    render_station_grid(stations)

    st.markdown("### Active charging sessions")
    if not sessions:
        st.info("No EV is charging at the moment.")
    else:
        table = pd.DataFrame({
            "Station": [x["station_name"] for x in sessions],
            "Vehicle No.": [x["plate"] for x in sessions],
            "Owner": [x["owner"] for x in sessions],
            "Started": [x["started_at"] for x in sessions],
            "Planned end": [x["planned_end"] for x in sessions],
            "Progress (%)": [int(x["progress"] * 100) for x in sessions],
            "Time left": [fmt_secs(x["remaining_seconds"]) for x in sessions],
            "Request": [x["request_id"] for x in sessions],
        })
        show_table(table)

        chart_df = pd.DataFrame({
            "Station": [x["station_name"] + " · " + x["plate"] for x in sessions],
            "Progress (%)": [round(x["progress"] * 100, 1) for x in sessions],
        })
        fig = px.bar(chart_df, x="Progress (%)", y="Station", orientation="h",
                     range_x=[0, 100], title="Charging progress",
                     color_discrete_sequence=[BLUES[0]])
        show_chart(style_fig(fig, height=max(220, 70 * len(sessions) + 100)))
    st.caption(f"Auto-refreshing every 2 seconds · last update {datetime.now():%H:%M:%S}")


def page_monitor() -> None:
    page_header("Live Monitoring", "Real-time view of chargers, sessions and the semaphore.")
    show_flash()
    monitor_live()


# ===========================================================================
# PAGE 7: Charging History
# ===========================================================================
def page_history() -> None:
    page_header("Charging History", "Every finished or cancelled request.")
    show_flash()
    rows = system.get_history()
    if not rows:
        st.info("No history yet. Completed and cancelled requests appear here.")
        return

    df = pd.DataFrame(rows)
    done = df[df["outcome"].isin(["completed", "stopped_early"])]
    row = st.columns(4)
    kpi(row[0], "Records", len(df), "")
    kpi(row[1], "Charging sessions", len(done), f"{len(df) - len(done)} cancelled")
    kpi(row[2], "Energy delivered", f"{round(done['energy_kwh'].fillna(0).sum(), 2)} kWh", "")
    avg_wait = done["wait_seconds"].dropna().mean() if len(done) else 0
    kpi(row[3], "Average wait", fmt_secs(0 if pd.isna(avg_wait) else avg_wait), "")
    st.write("")

    c1, c2, c3 = st.columns(3)
    outcomes = c1.multiselect(
        "Outcome", list(OUTCOME_LABEL), default=list(OUTCOME_LABEL),
        format_func=lambda o: OUTCOME_LABEL[o], key="hist_outcome",
    )
    station_names = sorted(df["station_name"].dropna().unique())
    station_choice = c2.selectbox("Station", ["All stations"] + station_names, key="hist_station")
    search = c3.text_input("Search vehicle or owner", key="hist_search")

    view = df[df["outcome"].isin(outcomes)]
    if station_choice != "All stations":
        view = view[view["station_name"] == station_choice]
    if search:
        needle = search.lower()
        view = view[
            view["plate"].str.lower().str.contains(needle)
            | view["owner"].str.lower().str.contains(needle)
        ]

    columns = [
        ("history_id", "History ID"), ("request_id", "Request"), ("plate", "Vehicle No."),
        ("owner", "Owner"), ("model", "Model"), ("station_name", "Station"),
        ("requested_at", "Requested"), ("started_at", "Started"), ("ended_at", "Ended"),
        ("duration_minutes", "Duration (min)"), ("wait_seconds", "Wait (s)"),
        ("charge_seconds", "Charged (s)"), ("energy_kwh", "Energy (kWh)"), ("outcome", "Outcome"),
    ]
    table = view[[c for c, _ in columns]].rename(columns=dict(columns))
    table["Station"] = table["Station"].fillna("-")
    table["Started"] = table["Started"].fillna("-")
    table["Outcome"] = table["Outcome"].map(OUTCOME_LABEL).fillna(table["Outcome"])

    show_table(table)
    st.caption(f"{len(table)} record(s) shown")
    st.download_button(
        "Download as CSV",
        data=table.to_csv(index=False).encode("utf-8"),
        file_name="charging_history.csv",
        mime="text/csv",
    )


# ===========================================================================
# PAGE 8: Analytics
# ===========================================================================
def page_analytics() -> None:
    page_header("Analytics", "Charts built from live system data.")
    show_flash()
    if st.button("Refresh data"):
        st.rerun()

    summary = system.get_summary()
    history = pd.DataFrame(system.get_history())

    row = st.columns(4)
    kpi(row[0], "Energy delivered", f"{summary['total_energy_kwh']} kWh", "")
    kpi(row[1], "Average wait", fmt_secs(summary["avg_wait_seconds"]), "")
    kpi(row[2], "Total requests", summary["total_requests"], "")
    kpi(row[3], "Utilization now", f"{summary['utilization_pct']}%", "")
    st.write("")

    c1, c2 = st.columns(2)
    with c1:
        status_df = pd.DataFrame({
            "Status": ["Available", "Charging", "Maintenance"],
            "Stations": [summary["available"], summary["occupied"], summary["maintenance"]],
        })
        fig = px.pie(status_df, names="Status", values="Stations", hole=0.55,
                     title="Station status", color="Status",
                     color_discrete_map={"Available": "#60a5fa", "Charging": "#1d4ed8",
                                         "Maintenance": "#f59e0b"})
        show_chart(style_fig(fig))
    with c2:
        req_df = pd.DataFrame({
            "Status": ["Waiting", "Charging", "Completed", "Cancelled"],
            "Requests": [summary["waiting"], summary["charging"],
                         summary["completed"], summary["cancelled"]],
        })
        fig = px.bar(req_df, x="Status", y="Requests", title="Requests by status",
                     color_discrete_sequence=[BLUES[0]], text="Requests")
        show_chart(style_fig(fig))

    c3, c4 = st.columns(2)
    with c3:
        gauge = go.Figure(go.Indicator(
            mode="gauge+number",
            value=summary["utilization_pct"],
            number={"suffix": "%"},
            title={"text": "Charger utilization"},
            gauge={
                "axis": {"range": [0, 100]},
                "bar": {"color": "#1d4ed8"},
                "steps": [{"range": [0, 50], "color": "#dbeafe"},
                          {"range": [50, 100], "color": "#bfdbfe"}],
            },
        ))
        show_chart(style_fig(gauge))
    with c4:
        if history.empty:
            st.info("Session charts appear after the first request is completed or cancelled.")
        else:
            outcome_df = history["outcome"].map(OUTCOME_LABEL).fillna(history["outcome"]) \
                .value_counts().reset_index()
            outcome_df.columns = ["Outcome", "Count"]
            fig = px.pie(outcome_df, names="Outcome", values="Count", hole=0.55,
                         title="Session outcomes", color_discrete_sequence=BLUES)
            show_chart(style_fig(fig))

    if history.empty:
        return
    done = history[history["outcome"].isin(["completed", "stopped_early"])].copy()
    if done.empty:
        st.info("No finished charging sessions yet.")
        return

    per_station = done.groupby("station_name").agg(
        Sessions=("history_id", "count"), Energy=("energy_kwh", "sum")
    ).reset_index().rename(columns={"station_name": "Station"})

    c5, c6 = st.columns(2)
    with c5:
        fig = px.bar(per_station, x="Station", y="Energy", title="Energy delivered per station (kWh)",
                     color_discrete_sequence=[BLUES[0]], text_auto=".1f")
        show_chart(style_fig(fig))
    with c6:
        fig = px.bar(per_station, x="Station", y="Sessions", title="Sessions per station",
                     color_discrete_sequence=[BLUES[1]], text="Sessions")
        show_chart(style_fig(fig))

    c7, c8 = st.columns(2)
    with c7:
        waits = done.dropna(subset=["wait_seconds"])
        fig = px.histogram(waits, x="wait_seconds", nbins=10,
                           title="Waiting time distribution (seconds)",
                           color_discrete_sequence=[BLUES[4]])
        fig.update_layout(xaxis_title="Wait (s)", yaxis_title="EVs")
        show_chart(style_fig(fig))
    with c8:
        done["ended_dt"] = pd.to_datetime(done["ended_at"])
        done = done.sort_values("ended_dt")
        done["Cumulative energy (kWh)"] = done["energy_kwh"].fillna(0).cumsum()
        fig = px.line(done, x="ended_dt", y="Cumulative energy (kWh)", markers=True,
                      title="Cumulative energy delivered", color_discrete_sequence=[BLUES[0]])
        fig.update_layout(xaxis_title="Time")
        show_chart(style_fig(fig))


# ===========================================================================
# Sidebar and navigation
# ===========================================================================
PAGES = {
    "Overview": page_overview,
    "EV Management": page_evs,
    "Charging Station Management": page_stations,
    "Charging Requests": page_requests,
    "Waiting Queue": page_queue,
    "Live Monitoring": page_monitor,
    "Charging History": page_history,
    "Analytics": page_analytics,
}


def _apply_speed() -> None:
    try:
        system.set_seconds_per_minute(st.session_state["speed_slider"])
        flash("success", "Demo speed updated for active and future charging sessions.")
    except BackendError as exc:
        flash("error", str(exc))


def _do_reset() -> None:
    system.reset_system()
    st.session_state["reset_ok"] = False
    flash("success", "System reset. The four default stations were recreated.")


def sidebar() -> str:
    with st.sidebar:
        st.markdown('<div class="brand">⚡ EV Charging<br>Allocation System</div>'
                    '<div class="brand-sub">Counting semaphore · FIFO queue</div>',
                    unsafe_allow_html=True)
        page = st.radio("Navigation", list(PAGES), label_visibility="collapsed", key="nav")
        st.markdown("---")

        s = system.get_summary()
        st.markdown(
            f"**Free permits:** {s['semaphore_permits']} / {s['total_stations'] - s['maintenance']}  \n"
            f"**Waiting:** {s['waiting']}  \n"
            f"**Charging:** {s['charging']}"
        )
        st.markdown("---")

        st.markdown("**Demo speed**")
        st.slider(
            "Real seconds per charging minute", 1, 60,
            value=system.get_seconds_per_minute(), key="speed_slider",
            help="1 = very fast demo, 60 = real time.",
        )
        st.button("Apply speed", on_click=_apply_speed)
        st.markdown("---")

        st.markdown("**Reset system**")
        confirm = st.checkbox("Erase ALL data", key="reset_ok")
        st.button("Reset all data", disabled=not confirm, on_click=_do_reset)
    return page


selected_page = sidebar()
PAGES[selected_page]()