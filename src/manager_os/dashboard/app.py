"""Streamlit multi-tab dashboard for Manager OS."""

from __future__ import annotations

import sys
from datetime import date, datetime
from pathlib import Path

import streamlit as st

# Ensure the src package is importable when run via streamlit
_SRC = Path(__file__).parent.parent.parent.parent
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from manager_os.config import get_settings
from manager_os.db import get_connection

# ------------------------------------------------------------------
# Page config
# ------------------------------------------------------------------

st.set_page_config(
    page_title="Manager OS",
    page_icon="🧠",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ------------------------------------------------------------------
# Shared state and connection
# ------------------------------------------------------------------


@st.cache_resource
def _get_conn():
    settings = get_settings()
    return get_connection(settings.db_path)


conn = _get_conn()

# ------------------------------------------------------------------
# Sidebar
# ------------------------------------------------------------------

with st.sidebar:
    st.title("🧠 Manager OS")
    selected_date = st.date_input("Date", value=date.today())
    min_severity = st.selectbox(
        "Min severity",
        options=["low", "medium", "high", "critical"],
        index=1,
    )
    st.divider()

    # Extraction failure badge
    try:
        fail_count = conn.execute(
            "SELECT COUNT(*) FROM extraction_failures WHERE status = 'pending_review'"
        ).fetchone()[0]
        if fail_count > 0:
            st.warning(f"⚠️ {fail_count} extraction failure(s)")
    except Exception:
        pass

    if st.button("🔄 Refresh data"):
        st.cache_data.clear()
        st.rerun()

# ------------------------------------------------------------------
# Tabs
# ------------------------------------------------------------------

tabs = st.tabs(["Today", "People", "Clients", "Deals", "Forecast", "Meeting Prep"])

# ------------------------------------------------------------------
# Tab 1 — Today
# ------------------------------------------------------------------

with tabs[0]:
    from manager_os.build.dashboard_data import (
        get_action_items_filtered,
        get_open_action_items,
        get_signal_counts,
        get_today_signals,
        update_action_item,
        update_signal_status,
    )

    @st.cache_data(ttl=300)
    def _today_signals(d, sev):
        return get_today_signals(conn, target_date=d, min_severity=sev)

    @st.cache_data(ttl=300)
    def _action_items(show_stale: bool = False, show_completed: bool = False):
        statuses = ["open"]
        if show_stale:
            statuses += ["stale", "not_mine"]
        if show_completed:
            statuses.append("completed")
        return get_action_items_filtered(conn, statuses=statuses)

    @st.cache_data(ttl=300)
    def _signal_counts():
        return get_signal_counts(conn)

    # Action item display filters (sidebar-like inline toggles)
    _show_stale = st.session_state.get("ai_show_stale", False)
    _show_completed = st.session_state.get("ai_show_completed", False)

    signals = _today_signals(selected_date, min_severity)
    action_items = _action_items(show_stale=_show_stale, show_completed=_show_completed)
    counts = _signal_counts()

    # Metrics row
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("🔴 Critical", counts.get("critical", 0))
    col2.metric("🟠 High", counts.get("high", 0))
    col3.metric("✅ Open Actions", len(action_items))
    col4.metric("📅 Meetings Today", conn.execute(
        "SELECT COUNT(*) FROM meetings WHERE meeting_date = ?", [selected_date]
    ).fetchone()[0])

    st.divider()

    if not signals:
        st.success("No open signals at this severity level. 🎉")
    else:
        # Group signals by type
        _SEVERITY_BADGE = {
            "critical": "🔴 CRITICAL",
            "high": "🟠 HIGH",
            "medium": "🟡 MEDIUM",
            "low": "⚪ LOW",
        }
        _SECTION_ORDER = [
            ("critical", "🔴 Critical — Immediate Action"),
            ("risk", "🚨 Delivery Risks"),
            ("people_health", "👥 People Needing Attention"),
            ("sow_loe_review", "📋 Deal / SOW / LOE Actions"),
            ("utilization_risk", "⚠️ Staffing / Utilization"),
            ("blocker", "🚧 Blockers"),
            ("follow_up", "📌 Follow-Ups"),
            ("stale_item", "🕰️ Stale Items"),
            ("ask", "❓ Asks"),
            ("decision", "⚖️ Decisions"),
            ("__other__", "📌 Other Signals"),
        ]

        # Build section buckets
        bucketed: dict[str, list] = {key: [] for key, _ in _SECTION_ORDER}
        for s in signals:
            if s.severity == "critical":
                bucketed["critical"].append(s)
            elif s.signal_type in bucketed:
                bucketed[s.signal_type].append(s)
            else:
                bucketed["__other__"].append(s)

        for bucket_key, section_title in _SECTION_ORDER:
            section_signals = bucketed.get(bucket_key, [])
            if not section_signals:
                continue

            st.subheader(section_title)
            for s in section_signals:
                badge = _SEVERITY_BADGE.get(s.severity, s.severity.upper())
                brief_id = f"signal:{s.id[:16]}"
                with st.expander(f"{badge} **{s.entity_name}** — {s.summary}", expanded=(s.severity == "critical")):
                    if s.why_it_matters:
                        st.markdown(f"*{s.why_it_matters}*")
                    meta_cols = st.columns(4)
                    meta_cols[0].caption(f"Type: `{s.signal_type}`")
                    meta_cols[1].caption(f"Source: `{s.source}`")
                    meta_cols[2].caption(f"Due: {s.due_date or 'none'}")
                    meta_cols[3].caption(f"ID: `{brief_id}`")

                    st.caption("**Feedback:**")
                    fb_cols = st.columns(7)
                    _FB_RATINGS = [
                        ("✅ Useful",           "useful",          "green"),
                        ("🔇 Noisy",            "noisy",           "orange"),
                        ("🕰️ Stale",            "stale",           "gray"),
                        ("❌ Wrong",            "wrong",           "red"),
                        ("🔍 Missing context",  "missing-context", "blue"),
                        ("✓ Acknowledge",       None,              None),
                        ("✕ Dismiss",           None,              None),
                    ]
                    for col_i, (label, fb_rating, _color) in enumerate(_FB_RATINGS):
                        key = f"fb_{s.id}_{col_i}"
                        if fb_cols[col_i].button(label, key=key):
                            if fb_rating:
                                try:
                                    from manager_os.build.feedback import mark as fb_mark
                                    fb_mark(conn, item_id=brief_id, rating=fb_rating,
                                            source_path=s.source_path,
                                            entity_name=s.entity_name,
                                            signal_type=s.signal_type)
                                except Exception:
                                    pass
                            elif label.startswith("✓"):
                                update_signal_status(conn, s.id, "acknowledged")
                            else:
                                update_signal_status(conn, s.id, "dismissed")
                            st.cache_data.clear()
                            st.rerun()

    # Action items section
    st.divider()
    ai_hdr_cols = st.columns([4, 1, 1])
    ai_hdr_cols[0].subheader("✅ Action Items")
    if ai_hdr_cols[1].checkbox("Show stale", key="ai_show_stale"):
        st.session_state["ai_show_stale"] = True
    if ai_hdr_cols[2].checkbox("Show done", key="ai_show_completed"):
        st.session_state["ai_show_completed"] = True

    if not action_items:
        st.info("No open action items matching the current filter.")
    else:
        _AI_FB_RATINGS = [
            ("✅ Complete",        "completed",       None),
            ("🔇 Noisy",          "noisy",           "noisy"),
            ("🕰️ Stale",          "stale",           "stale"),
            ("❌ Wrong",          "wrong",           "wrong"),
            ("🔍 Missing context","missing-context", "missing-context"),
            ("🚫 Not mine",       "not_mine",        None),
        ]
        for ai in action_items:
            brief_id = f"action:{ai.id[:16]}"
            status_icon = {"open": "☐", "completed": "✅", "stale": "🕰️",
                           "dismissed": "✕", "snoozed": "💤", "not_mine": "🚫",
                           "done": "✅"}.get(ai.status, "☐")
            due_str = f"  *(by {ai.due_date})*" if ai.due_date else ""
            fb_badge = f"  [{ai.feedback_rating}]" if ai.feedback_rating else ""
            label = f"{status_icon} **{ai.assigned_to}**: {ai.description[:80]}{'…' if len(ai.description) > 80 else ''}{due_str}{fb_badge}"
            with st.expander(label, expanded=False):
                st.caption(f"ID: `{brief_id}`  •  Status: `{ai.status}`")
                if ai.due_date:
                    st.caption(f"Due: {ai.due_date}")
                if ai.feedback_reason:
                    st.caption(f"Reason: {ai.feedback_reason}")
                # Full description if truncated
                if len(ai.description) > 80:
                    st.markdown(f"_{ai.description}_")

                btn_cols = st.columns(len(_AI_FB_RATINGS) + 1)
                for col_i, (lbl, new_status, fb_rating) in enumerate(_AI_FB_RATINGS):
                    if btn_cols[col_i].button(lbl, key=f"ai_{ai.id}_{col_i}"):
                        update_action_item(
                            conn,
                            ai.id,
                            status=new_status,
                            feedback_rating=fb_rating,
                        )
                        st.cache_data.clear()
                        st.rerun()
                # Snooze button (last column)
                if btn_cols[len(_AI_FB_RATINGS)].button("💤 Snooze 7d", key=f"ai_snooze_{ai.id}"):
                    from datetime import timedelta
                    snooze_to = date.today() + timedelta(days=7)
                    update_action_item(conn, ai.id, status="snoozed", snooze_until=snooze_to)
                    st.cache_data.clear()
                    st.rerun()

# ------------------------------------------------------------------
# Tab 2 — People
# ------------------------------------------------------------------

with tabs[1]:
    from manager_os.build.dashboard_data import get_people_rows, get_signals_for_person

    @st.cache_data(ttl=300)
    def _people_rows(d):
        return get_people_rows(conn, as_of=d)

    people_rows = _people_rows(selected_date)

    if not people_rows:
        st.info("No people data found. Run `manager-os ingest` and `manager-os extract` first.")
    else:
        _MORALE_BADGE = {"red": "🔴", "yellow": "🟡", "green": "🟢"}
        _SEV_BADGE = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "⚪"}

        # Filter sidebar
        morale_filter = st.multiselect(
            "Filter by morale", options=["red", "yellow", "green"],
            default=["red", "yellow", "green"],
        )

        filtered = [p for p in people_rows if p.morale in morale_filter]

        for p in filtered:
            badge = _MORALE_BADGE.get(p.morale, "⚪")
            header = f"{badge} **{p.name}**"
            if p.role:
                header += f"  •  {p.role}"
            if p.current_client:
                header += f"  •  📌 {p.current_client}"
            if p.allocation_pct:
                header += f"  •  {p.allocation_pct:.0f}%"
            sev_flag = ""
            if p.highest_severity in ("critical", "high"):
                sev_flag = f"  {_SEV_BADGE.get(p.highest_severity, '')} {p.open_signal_count} signal(s)"

            with st.expander(header + sev_flag, expanded=(p.morale == "red")):
                c1, c2, c3, c4 = st.columns(4)
                c1.metric("Days since 1:1", p.days_since_1on1 if p.days_since_1on1 is not None else "—")
                c2.metric("Open signals", p.open_signal_count)
                c3.metric("Allocation", f"{p.allocation_pct:.0f}%" if p.allocation_pct else "—")
                c4.metric("Morale", p.morale.title())

                if p.blockers:
                    st.warning(f"🚧 Blockers: {p.blockers}")
                if p.growth_topic:
                    st.caption(f"Growth: {p.growth_topic}")

                person_signals = get_signals_for_person(conn, p.name)
                if person_signals:
                    st.markdown("**Active signals:**")
                    for s in person_signals:
                        sev = _SEV_BADGE.get(s.severity, "")
                        st.markdown(f"  {sev} `{s.signal_type}` — {s.summary}")

# ------------------------------------------------------------------
# Tab 3 — Clients
# ------------------------------------------------------------------

with tabs[2]:
    from manager_os.build.dashboard_data import get_client_rows, get_signals_for_client

    @st.cache_data(ttl=300)
    def _client_rows(d):
        return get_client_rows(conn, as_of=d)

    client_rows = _client_rows(selected_date)

    if not client_rows:
        st.info("No client data found. Run `manager-os ingest` and `manager-os extract` first.")
    else:
        _HEALTH_BADGE = {"red": "🔴", "yellow": "🟡", "green": "🟢"}
        _SEV_BADGE2 = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "⚪"}

        health_filter = st.multiselect(
            "Filter by health", options=["red", "yellow", "green"],
            default=["red", "yellow", "green"],
        )

        filtered_clients = [c for c in client_rows if c["health"] in health_filter]

        for c in filtered_clients:
            badge = _HEALTH_BADGE.get(c["health"], "⚪")
            last_str = f"  •  last update: {c['last_update_date']}" if c.get("last_update_date") else ""
            header = f"{badge} **{c['name']}**{last_str}"
            if c["open_signal_count"]:
                sev = _SEV_BADGE2.get(c.get("highest_severity", ""), "")
                header += f"  {sev} {c['open_signal_count']} signal(s)"

            with st.expander(header, expanded=(c["health"] == "red")):
                m1, m2, m3 = st.columns(3)
                m1.metric("Health", c["health"].title())
                m2.metric("Open signals", c["open_signal_count"])
                m3.metric("Open risks", c.get("open_risk_count", 0))

                client_sigs = get_signals_for_client(conn, c["name"])
                if client_sigs:
                    st.markdown("**Active signals:**")
                    for s in client_sigs:
                        sev = _SEV_BADGE2.get(s.severity, "")
                        st.markdown(f"  {sev} `{s.signal_type}` — {s.summary}")

# ------------------------------------------------------------------
# Tab 4 — Deals
# ------------------------------------------------------------------

with tabs[3]:
    from manager_os.build.dashboard_data import get_deal_rows

    @st.cache_data(ttl=300)
    def _deal_rows(d):
        return get_deal_rows(conn, as_of=d)

    deal_rows = _deal_rows(selected_date)

    if not deal_rows:
        st.info("No deal data found. Run `manager-os ingest --source deals` first.")
    else:
        # Sidebar filters
        stage_options = sorted({r.stage for r in deal_rows if r.stage})
        feas_options = sorted({r.staffing_feasibility for r in deal_rows if r.staffing_feasibility})
        filter_stage = st.multiselect("Filter by stage", options=stage_options, default=stage_options)
        filter_feas = st.multiselect("Filter feasibility", options=feas_options, default=feas_options)

        filtered_deals = [
            r for r in deal_rows
            if (not filter_stage or r.stage in filter_stage)
            and (not filter_feas or r.staffing_feasibility in filter_feas)
        ]

        _SEV_BADGE3 = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "⚪"}

        for d in filtered_deals:
            urgent = (
                d.days_to_close is not None
                and d.days_to_close <= 7
                and d.sow_status not in ("signed", "complete")
            )
            days_str = ""
            if d.days_to_close is not None:
                color = "🔴" if urgent else ("🟡" if d.days_to_close <= 14 else "🟢")
                days_str = f" {color} {d.days_to_close}d"

            sev_str = ""
            if d.highest_severity:
                sev_str = f"  {_SEV_BADGE3.get(d.highest_severity, '')} {d.open_signal_count} signal(s)"

            header = f"**{d.deal_name}** — `{d.stage}`{days_str}{sev_str}"

            with st.expander(header, expanded=urgent):
                c1, c2, c3, c4 = st.columns(4)
                c1.metric("Close date", str(d.close_date) if d.close_date else "—")
                c2.metric("SOW", d.sow_status or "—")
                c3.metric("LOE", d.loe_status or "—")
                c4.metric("Staffing", d.staffing_feasibility or "—")

                if d.blockers:
                    st.warning(f"🚧 {d.blockers}")
                if d.next_action:
                    st.info(f"Next action: {d.next_action}")
                meta_cols = st.columns(2)
                meta_cols[0].caption(f"Account: {d.account}")
                meta_cols[1].caption(f"Owner: {d.technical_owner}")

# ------------------------------------------------------------------
# Tab 5 — Forecast
# ------------------------------------------------------------------

with tabs[4]:
    from manager_os.build.dashboard_data import (
        get_forecast_rows,
        get_forecast_summary,
        get_forecast_week_list,
        get_people_allocation_for_week,
    )

    @st.cache_data(ttl=300)
    def _forecast_weeks(d):
        return get_forecast_week_list(conn, as_of=d)

    @st.cache_data(ttl=300)
    def _forecast_rows(d):
        return get_forecast_rows(conn, as_of=d)

    @st.cache_data(ttl=300)
    def _forecast_summary(d):
        return get_forecast_summary(conn, as_of=d)

    @st.cache_data(ttl=300)
    def _week_alloc(week):
        return get_people_allocation_for_week(conn, week_start=week)

    forecast_weeks = _forecast_weeks(selected_date)
    forecast_rows = _forecast_rows(selected_date)
    forecast_summary = _forecast_summary(selected_date)

    if not forecast_rows:
        st.info("No forecast data. Run `manager-os ingest --source forecast` first.")
    else:
        # ---- Week selector ----
        st.subheader("Week Allocation")
        week_options = [str(w) for w in forecast_weeks]
        default_idx = 0  # nearest week
        if week_options:
            col_wk1, col_wk2, col_wk3 = st.columns([3, 1, 1])
            with col_wk1:
                selected_week_str = st.selectbox(
                    "Forecast week",
                    options=week_options,
                    index=default_idx,
                    key="fc_week_sel",
                )
            from datetime import date as _date
            selected_week = _date.fromisoformat(selected_week_str) if selected_week_str else (forecast_weeks[0] if forecast_weeks else selected_date)

            week_alloc = _week_alloc(selected_week)
            if week_alloc:
                import pandas as pd
                rows_disp = []
                for wa in week_alloc:
                    pct = wa["allocation_pct"]
                    planned_h = wa["planned_hours"]
                    target_h = wa["target_hours"]
                    warn = wa["warning"] or ""
                    if target_h and target_h > 0:
                        hours_str = f"{planned_h:.0f} / {target_h:.0f} hrs"
                        pct_str = f"{pct:.0f}%"
                    else:
                        hours_str = f"{planned_h:.0f} hrs"
                        pct_str = "no target"
                    badge = "🔴" if pct > 100 else ("🟡" if pct < 50 and target_h else "🟢")
                    rows_disp.append({
                        "": badge,
                        "Person": wa["person_name"],
                        "Hours": hours_str,
                        "Allocation": pct_str,
                        "Projects": ", ".join(wa["projects"]),
                        "Warning": warn,
                    })
                df_week = pd.DataFrame(rows_disp)
                st.dataframe(df_week, use_container_width=True, hide_index=True)
            else:
                st.info(f"No forecast rows for week {selected_week}.")

        st.divider()

        # Summary buckets
        st.subheader("Staffing Summary")
        for label in ("2w", "30d", "60d"):
            bucket = forecast_summary.get(label, {})
            over = bucket.get("overallocated", [])
            under = bucket.get("underallocated", [])
            avail = bucket.get("available", [])

            with st.expander(f"**{label} window**  •  🔴 {len(over)} over  •  🟡 {len(under)} under  •  🟢 {len(avail)} OK",
                             expanded=(label == "2w")):
                cols = st.columns(3)
                with cols[0]:
                    st.markdown("**🔴 Overallocated (>100%)**")
                    st.markdown("\n".join(f"- {p}" for p in over) or "*None*")
                with cols[1]:
                    st.markdown("**🟡 Underallocated (<50%)**")
                    st.markdown("\n".join(f"- {p}" for p in under) or "*None*")
                with cols[2]:
                    st.markdown("**🟢 Available (50–100%)**")
                    st.markdown("\n".join(f"- {p}" for p in avail) or "*None*")

        st.divider()

        # Detailed table (multi-week view)
        st.subheader("All Weeks — Allocation Detail")

        all_people = sorted({r.person_name for r in forecast_rows})
        selected_people = st.multiselect("Filter person", options=all_people, default=all_people)
        show_only_issues = st.checkbox("Show only over/under-allocated", value=False)

        filtered_fc = [
            r for r in forecast_rows
            if r.person_name in selected_people
            and (not show_only_issues or r.is_overallocated or r.is_underallocated)
        ]

        if filtered_fc:
            import pandas as pd
            df = pd.DataFrame([{
                "Person": r.person_name,
                "Week": str(r.week_start),
                "Client": r.client,
                "Project": r.project,
                "Allocation %": f"{r.allocation_pct:.0f}%",
                "Type": r.forecast_type,
                "Issue": "🔴 Over" if r.is_overallocated else ("🟡 Under" if r.is_underallocated else ""),
            } for r in filtered_fc])
            st.dataframe(df, use_container_width=True, hide_index=True)

# ------------------------------------------------------------------
# Tab 6 — Meeting Prep
# ------------------------------------------------------------------

with tabs[5]:
    from manager_os.build.dashboard_data import get_meetings_for_date

    @st.cache_data(ttl=300)
    def _meetings(d):
        return get_meetings_for_date(conn, target_date=d)

    today_meetings = _meetings(selected_date)

    if not today_meetings:
        st.info(f"No meetings found for {selected_date}. Add them to the meetings table first.")
    else:
        st.subheader(f"Meetings on {selected_date}")

        meeting_titles = [f"{m['start_time'] or '?:??'} — {m['title']}" for m in today_meetings]
        selected_idx = st.selectbox("Select meeting", options=list(range(len(today_meetings))),
                                    format_func=lambda i: meeting_titles[i])

        chosen = today_meetings[selected_idx]

        col1, col2 = st.columns([3, 1])
        with col1:
            st.markdown(f"### {chosen['title']}")
            if chosen.get("attendees"):
                st.caption("Attendees: " + ", ".join(chosen["attendees"]))

        with col2:
            use_llm = st.checkbox("🤖 Enrich with LLM", value=False,
                                  help="Requires OPENAI_API_KEY in your environment")
            gen_btn = st.button("🔄 Generate / Refresh Prep", type="primary")

        if gen_btn:
            try:
                from manager_os.config import get_settings, load_clients, load_deal_aliases, load_people
                from manager_os.extract.entities import EntityResolver
                from manager_os.extract.meeting_prep import (
                    generate_meeting_prep,
                    enrich_meeting_prep_with_llm,
                )

                settings = get_settings()
                resolver = EntityResolver(
                    load_people(settings), load_clients(settings), load_deal_aliases(settings)
                )
                prep = generate_meeting_prep(chosen, conn, resolver)
                if use_llm:
                    prep = enrich_meeting_prep_with_llm(prep, conn)
                st.cache_data.clear()
                st.success("Prep generated!" + (" (LLM enriched)" if use_llm else ""))
            except Exception as exc:
                st.error(f"Error generating prep: {exc}")

        # Show stored prep if available
        prep_row = conn.execute(
            "SELECT content, generated_at FROM meeting_prep WHERE meeting_id = ?",
            [chosen.id]
        ).fetchone()
        if prep_row:
            st.caption(f"Generated at: {prep_row[1]}")
            st.markdown(prep_row[0])
        else:
            st.info("Click 'Generate / Refresh Prep' to build the prep document for this meeting.")

