"""
SAE Streamlit Dashboard — rebuilt using SAEAdapter via engine.py.
Run:  streamlit run sae/dashboard.py
"""

from __future__ import annotations

import math
from functools import partial

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from engines.sae.engine import (
    analyze_student,
    get_advisor_overview,
    get_course_risk,
    simulate_gpa,
    get_advisor_analysis,
    get_cohort_stats,
)
from engines.sae.rule_engine import f_generate_talking_points

# ── Page config ────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="SAE — Student Analysis Engine",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── CSS / dark theme ──────────────────────────────────────────────────────────
st.markdown("""<style>
.stApp { background-color:#0E1117; color:#FAFAFA; }
section[data-testid="stSidebar"] { background-color:#161B27; }
div[data-testid="metric-container"] {
    background:#1E2130; border-radius:10px; padding:14px; border:1px solid #2D3250;
}
.banner {
    background:linear-gradient(135deg,#1A1F35 0%,#1E2540 100%);
    border-radius:14px; padding:24px 28px; border:1px solid #2D3250; margin-bottom:16px;
}
.smc { background:#1E2130; border-radius:10px; padding:16px 20px;
       border:1px solid #2D3250; text-align:center; margin-bottom:8px; }
.atc { background:#1E1020; border-radius:10px; padding:14px 18px;
       border-left:4px solid #E53935; margin:6px 0; }
.alw { background:#2D2200; border-left:4px solid #FB8C00; color:#FFD180;
       border-radius:6px; padding:12px 16px; margin:6px 0; }
.alh { background:#2D0A0A; border-left:4px solid #E53935; color:#FF8A80;
       border-radius:6px; padding:12px 16px; margin:6px 0; }
.als { background:#0A2D0A; border-left:4px solid #43A047; color:#A5D6A7;
       border-radius:6px; padding:12px 16px; margin:6px 0; }
.isd { background:#1A1A2E; border-radius:8px; padding:14px 18px; color:#8B9DC0;
       font-style:italic; margin:8px 0; border:1px dashed #2D3250; }
.card-box { background:#1E2130; border-radius:10px; padding:18px 20px;
            border:1px solid #2D3250; height:100%; }
.sim-result { background:#1A2540; border-radius:10px; padding:18px 22px;
              border:1px solid #2D4080; margin-top:12px; }
.amber-box { background:#2D2200; border-left:4px solid #FB8C00; color:#FFD180;
             border-radius:6px; padding:12px 16px; margin:6px 0; }
</style>""", unsafe_allow_html=True)

# ── Colour constants ───────────────────────────────────────────────────────────
_HEX      = {"high": "#E53935", "moderate": "#FB8C00", "low": "#43A047"}
_BADGE_BG = {"high": "#4A1010", "moderate": "#4A3000", "low": "#0A3020"}
_BADGE_FG = {"high": "#FF8A80", "moderate": "#FFD180", "low": "#A5D6A7"}

_PREFIX = "Faculty of Computing and Information Sciences -"
_GRADE_LETTERS = {"A+", "A", "A-", "B+", "B", "B-", "C+", "C", "C-", "D+", "D", "D-", "F"}


# ── Shared helpers ────────────────────────────────────────────────────────────

def _na(val: object) -> str:
    if val is None:
        return "N/A"
    try:
        if pd.isna(val):
            return "N/A"
    except (TypeError, ValueError):
        pass
    s = str(val).strip()
    return "N/A" if s in ("", "—", "nan", "None") else s


def _fmt_cgpa(val: object) -> str:
    try:
        f = float(val)  # type: ignore[arg-type]
        return f"{f:.2f}" if math.isfinite(f) else "N/A"
    except (TypeError, ValueError):
        return "N/A"


def _shorten(prog: object) -> str:
    s = str(prog or "").strip()
    if s.startswith(_PREFIX):
        return s[len(_PREFIX):].strip()
    return s or "N/A"


def _risk_badge(rl: str) -> str:
    labels = {"low": "On Track", "moderate": "Needs Attention", "high": "At Risk"}
    bg, fg = _BADGE_BG.get(rl, "#333"), _BADGE_FG.get(rl, "#EEE")
    txt = labels.get(rl, "N/A")
    return (
        f'<span style="background:{bg};color:{fg};padding:8px 18px;'
        f'border-radius:6px;font-weight:bold;font-size:1em;'
        f'white-space:nowrap">{txt}</span>'
    )


def _metric_card(title: str, value: str, color: str = "#FAFAFA") -> str:
    return (
        f'<div class="smc">'
        f'<div style="font-size:0.74em;color:#8B9DC0;letter-spacing:.07em;margin-bottom:6px">'
        f'{title}</div>'
        f'<div style="font-size:2em;font-weight:bold;color:{color}">{value}</div>'
        f'</div>'
    )


def _cgpa_color(cgpa: "float | None") -> str:
    if cgpa is None:
        return "#8B9DC0"
    return "#E53935" if cgpa < 2.0 else "#FB8C00" if cgpa < 2.5 else "#43A047"


def _pct_to_letter(pct: float, grade_thresholds: dict) -> str:
    ordered = sorted(grade_thresholds.items(), key=lambda x: -x[1])
    for letter, threshold in ordered:
        if pct >= threshold:
            return letter
    return "F"


def _sem_ordinal_dash(sem: str) -> int:
    parts = str(sem).strip().split()
    if len(parts) < 2:
        return 0
    _s = {"Spring": 0, "Summer": 1, "Fall": 2}
    try:
        return int(parts[1]) * 3 + _s.get(parts[0], 0)
    except ValueError:
        return 0


def _advance_sem(sem: str, n: int) -> str:
    _idx = {0: "Spring", 1: "Summer", 2: "Fall"}
    new_ord = _sem_ordinal_dash(sem) + n
    return f"{_idx[new_ord % 3]} {new_ord // 3}"


def _advisor_for_student(student_id: str) -> str:
    """Deterministic advisor assignment: same formula as the provider."""
    idx = sum(ord(c) for c in str(student_id)) % 10
    return f"ADV{str(idx + 1).zfill(3)}"


def _risk_explanation_expander(risk_breakdown: dict, cgpa: "float | None") -> None:
    """Expandable 'How is this calculated?' section for the risk badge."""
    with st.expander("How is this calculated?"):
        st.markdown(
            "| Criterion | Points |\n"
            "|:---|---:|\n"
            "| Academic Warning active | +40 |\n"
            "| Low first-attempt pass rate (below 75%) | +25 |\n"
            "| High failure rate (above 20%) | +25 |\n"
            "| Declining CGPA trend | +20 |\n"
        )
        st.caption("**Override:** CGPA below 2.0 forces score to minimum 70 (At Risk)")
        st.markdown("**Risk levels:** At Risk: 70–100 · Needs Attention: 40–69 · On Track: 0–39")

        _BD_LABELS: dict[str, str] = {
            "warning_risk":    "Academic Warning active",
            "low_pass_rate":   "Low first-attempt pass rate (< 75%)",
            "excess_failures": "High failure rate (> 20%)",
            "gpa_declining":   "Declining CGPA trend",
        }
        triggered = []
        for key, pts in risk_breakdown.items():
            label = _BD_LABELS.get(key, key.replace("_", " ").title())
            triggered.append(f"- **{label}:** +{pts} pts")
        if cgpa is not None and cgpa < 2.0:
            triggered.append("- **CGPA below 2.0:** score forced to ≥ 70 (At Risk)")

        if triggered:
            st.markdown("---")
            st.markdown("**Triggered for this student:**")
            for line in triggered:
                st.markdown(line)
        else:
            st.markdown("---")
            st.markdown("*No risk criteria triggered for this student.*")


# ── Cached data loaders ───────────────────────────────────────────────────────

@st.cache_data(ttl=3600, show_spinner="Loading advisor overview…")
def _load_overview() -> list[dict]:
    return get_advisor_overview()


@st.cache_data(ttl=300, show_spinner="Analyzing student…")
def _analyze(sid: str) -> dict:
    return analyze_student(sid)


@st.cache_data(ttl=3600, show_spinner="Computing course risk…")
def _get_course_risk_cached(level: "str | None" = None) -> list[dict]:
    return get_course_risk(level)


@st.cache_data(ttl=300, show_spinner="Building advisor analysis…")
def _get_advisor_analysis(sid: str) -> dict:
    return get_advisor_analysis(sid)


@st.cache_data(ttl=300, show_spinner=False)
def _get_cohort_stats(sid: str) -> dict:
    return get_cohort_stats(sid)


# ── LLM helpers (with session state caching) ──────────────────────────────────

def _get_student_focus(
    sid: str,
    result: dict,
    key_pts: "list | None" = None,
    category_performance: "dict | None" = None,
    rules: "dict | None" = None,
) -> "str | None":
    cache_key = f"focus_{sid}"
    if cache_key in st.session_state:
        return st.session_state[cache_key]

    try:
        from engines.sae.llm_advisor import generate_student_focus_statement
        profile = result.get("profile", {})
        cgpa    = result.get("official_cgpa") or 0
        credits = int(profile.get("official_credits_passed") or 0)
        kp      = key_pts or result.get("key_points") or []
        with st.spinner("Writing your personal focus…"):
            para = generate_student_focus_statement(
                profile, kp, cgpa, credits, category_performance, rules
            )
        if para:
            st.session_state[cache_key] = para
            return para
    except Exception:
        pass
    return None


def _get_advisor_session_guide(
    sid: str,
    aa_data: dict,
    category_performance: "dict | None" = None,
) -> "dict | None":
    cache_key = f"session_guide_{sid}"
    if cache_key in st.session_state:
        return st.session_state[cache_key]

    try:
        from engines.sae.llm_advisor import generate_advisor_session_guide
        profile      = aa_data.get("profile", {})
        key_pts      = aa_data.get("key_points") or []
        cgpa         = aa_data.get("official_cgpa") or 0
        credits      = int(profile.get("official_credits_passed") or 0)
        cohort_stats = aa_data.get("cohort_stats") or {}
        with st.spinner("Preparing session guide…"):
            guide = generate_advisor_session_guide(
                profile, key_pts, cgpa, credits, cohort_stats, category_performance
            )
        if guide:
            st.session_state[cache_key] = guide
            return guide
    except Exception:
        pass
    return None


# ══════════════════════════════════════════════════════════════════════════════
# PAGE 1 — Student View
# ══════════════════════════════════════════════════════════════════════════════

def page_student_view() -> None:
    # Phase D fix: pre-fill from advisor navigation using value= parameter
    default_id = st.session_state.pop("prefill_student_id", "")
    if default_id:
        # Clear existing widget key to allow value= to take effect
        st.session_state.pop("sv_sid", None)

    sid = st.text_input(
        "Student ID",
        placeholder="e.g. STU000026",
        key="sv_sid",
        value=default_id if default_id else "",
    ).strip()

    # If prefill was set but text_input didn't pick it up, use it directly
    if not sid and default_id:
        sid = default_id

    if not sid:
        st.info("Enter a Student ID above to load the full profile.")
        return

    result = _analyze(sid)
    if "error" in result:
        st.markdown(
            f'<div class="alh"><strong>Student not found:</strong> '
            f'No record for ID <code>{sid}</code>. Check the ID and try again.</div>',
            unsafe_allow_html=True,
        )
        return

    # Clear simulator state when student changes
    if st.session_state.get("_last_sv_sid") != sid:
        st.session_state["_last_sv_sid"] = sid
        for k in list(st.session_state.keys()):
            if k.startswith("sim_letter_") or k.startswith("sim_pct_") or k == "sim_result":
                del st.session_state[k]
        # Clear LLM cache when student changes
        st.session_state.pop(f"recs_{sid}", None)

    profile  = result["profile"]
    rl       = result["risk_level"]
    cgpa     = result["official_cgpa"]
    creds    = profile.get("official_credits_passed")
    rules    = result["rules"]
    total_req = rules.get("total_credits_required", 133)

    # ── Section 1: Header ─────────────────────────────────────────────────────
    cgpa_str   = _fmt_cgpa(cgpa)
    creds_str  = str(int(creds)) if creds is not None else "N/A"
    cgpa_color = _cgpa_color(cgpa)

    st.markdown(
        f'<div class="banner">'
        f'<div style="display:flex;justify-content:space-between;align-items:flex-start">'
        f'<div>'
        f'<div style="font-size:1.75em;font-weight:bold;color:#FAFAFA;line-height:1.2">'
        f'{_na(profile.get("name"))}</div>'
        f'<div style="color:#8B9DC0;font-size:0.9em;margin-top:6px">'
        f'{_shorten(profile.get("program"))}</div>'
        f'<div style="color:#6B7EA0;font-size:0.85em;margin-top:3px">'
        f'{_na(profile.get("level"))}</div>'
        f'<div style="margin-top:12px;display:flex;gap:24px;align-items:center">'
        f'<span style="color:#8B9DC0;font-size:0.9em">CGPA: '
        f'<strong style="color:{cgpa_color};font-size:1.2em">{cgpa_str}</strong></span>'
        f'<span style="color:#8B9DC0;font-size:0.9em">Credits: '
        f'<strong style="color:#FAFAFA;font-size:1.1em">{creds_str} / {total_req}</strong></span>'
        f'</div></div>'
        f'<div style="padding-top:6px">{_risk_badge(rl)}</div>'
        f'</div></div>',
        unsafe_allow_html=True,
    )

    # Plain-English reason box driven by risk flags
    if rl != "low":
        risk_flags_sv = result.get("risk_flags") or []
        flag_types_sv = {f["type"] for f in risk_flags_sv}
        _reason = None
        if "dismissal_active" in flag_types_sv:
            _reason = (
                "You have 4 or more consecutive academic warnings. "
                "Program dismissal is currently in effect — faculty council approval is required to continue."
            )
        elif "dismissal_risk" in flag_types_sv:
            _reason = (
                "You have 3 consecutive academic warnings. "
                "One more warning results in automatic program dismissal."
            )
        elif "cgpa_below_minimum" in flag_types_sv:
            _reason = (
                "Your cumulative GPA is below the university minimum of 2.0. "
                "You cannot register for graduation project or field training until this is resolved."
            )
        elif "active_warning" in flag_types_sv:
            _reason = (
                "You have an active academic warning on your record. "
                "Continued poor performance may result in suspension."
            )
        elif "cgpa_approaching_minimum" in flag_types_sv:
            _reason = (
                "Your CGPA is declining and approaching the 2.0 minimum. "
                "Early intervention is important to prevent academic difficulty."
            )
        if _reason:
            _box_cls = "alh" if rl == "high" else "alw"
            st.markdown(f'<div class="{_box_cls}">{_reason}</div>', unsafe_allow_html=True)

    # ── Section 2: CGPA Over Time ─────────────────────────────────────────────
    st.markdown("##### Cumulative GPA Progression")
    trend = result.get("cgpa_trend_history") or []
    graded_trend = [t for t in trend if t.get("cgpa_at_end_of_semester", 0) > 0]

    if len(graded_trend) < 2:
        st.markdown(
            '<div class="isd">Insufficient history — fewer than 2 semesters recorded.</div>',
            unsafe_allow_html=True,
        )
    else:
        x_vals = [t["semester_label"] for t in graded_trend]
        y_vals = [round(t["cgpa_at_end_of_semester"], 2) for t in graded_trend]

        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=x_vals, y=y_vals,
            mode="lines+markers",
            line={"color": "#4C9BE8", "width": 2.5},
            marker={"size": 8, "color": "#4C9BE8", "line": {"color": "#FAFAFA", "width": 1}},
            hovertemplate="%{x}<br>CGPA: %{y:.2f}<extra></extra>",
            name="Cumulative GPA",
        ))
        fig.add_hline(
            y=2.0, line_dash="dash", line_color="#E53935", line_width=1.5,
            annotation_text="Minimum CGPA", annotation_position="bottom right",
            annotation_font_color="#E53935",
        )
        fig.update_layout(
            template="plotly_dark", height=260,
            yaxis={"range": [0, 4.3], "title": "CGPA", "gridcolor": "#2D3250"},
            xaxis={"title": "", "tickangle": -35, "gridcolor": "#2D3250"},
            margin=dict(l=0, r=80, t=10, b=60),
            plot_bgcolor="#1E2130", paper_bgcolor="#1E2130", showlegend=False,
        )
        st.plotly_chart(fig, use_container_width=True)

    st.divider()

    # ── Section 3: Credits Progress + Cohort Standing ─────────────────────────
    col_cred, col_cohort = st.columns(2)

    with col_cred:
        st.markdown("##### Credits Progress")
        phs = creds if creds is not None else 0
        pct_bar = min(phs / total_req * 100, 100) if total_req else 0
        bar_color = "#43A047" if pct_bar > 75 else "#FB8C00" if pct_bar >= 50 else "#E53935"

        st.markdown(
            f'<div class="card-box">'
            f'<div style="font-size:2em;font-weight:bold;color:#FAFAFA;margin-bottom:4px">'
            f'{int(phs)} <span style="font-size:0.5em;color:#8B9DC0">completed</span></div>'
            f'<div style="color:#8B9DC0;font-size:0.88em;margin-bottom:10px">'
            f'out of {total_req} required credits</div>'
            f'<div style="height:8px;background:#2D3250;border-radius:4px">'
            f'<div style="width:{pct_bar:.0f}%;height:8px;background:{bar_color};'
            f'border-radius:4px"></div></div>'
            f'</div>',
            unsafe_allow_html=True,
        )

    with col_cohort:
        st.markdown("##### Cohort Standing")
        cs = result.get("cohort_stats") or {}
        c_size = cs.get("cohort_size")
        c_avg  = cs.get("cohort_avg_cgpa")
        s_pct  = cs.get("student_percentile")

        if c_size and s_pct is not None:
            if s_pct >= 50:
                rank_label = f"Top {100 - s_pct:.0f}%"
                rank_color = "#43A047"
            else:
                rank_label = f"Bottom {s_pct:.0f}%"
                rank_color = "#E53935"
        else:
            rank_label, rank_color = "N/A", "#8B9DC0"

        st.markdown(
            f'<div class="card-box">'
            f'<div style="color:#8B9DC0;font-size:0.84em;margin-bottom:10px">'
            f'{_na(profile.get("level"))} · {c_size or "N/A"} students</div>'
            f'<div style="font-size:1.7em;font-weight:bold;color:{rank_color}">'
            f'{rank_label} of students</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

    st.divider()

    # ── Section 4: Current Semester + GPA Simulator ───────────────────────────
    st.markdown("##### Current Semester — CGPA Impact Simulator")

    current_courses = result.get("current_courses") or []
    if not current_courses:
        st.markdown(
            '<div class="isd">No active registrations found for current semester.</div>',
            unsafe_allow_html=True,
        )
    else:
        grade_thresholds = rules.get("grade_thresholds", {})

        for c in current_courses:
            code = c["course_code"]
            if f"sim_letter_{code}" not in st.session_state:
                lg = c.get("letter_grade") or ""
                st.session_state[f"sim_letter_{code}"] = lg
            if f"sim_pct_{code}" not in st.session_state:
                st.session_state[f"sim_pct_{code}"] = 0.0

        def _cb_pct_to_letter(code: str) -> None:
            pct = st.session_state.get(f"sim_pct_{code}", 0) or 0
            if float(pct) > 0:
                st.session_state[f"sim_letter_{code}"] = _pct_to_letter(
                    float(pct), grade_thresholds
                )

        st.caption(f"{len(current_courses)} course(s) in current semester")

        for c in current_courses:
            code = c["course_code"]
            ch   = c.get("credit_hours", 3)
            lg   = c.get("letter_grade")

            col_code, col_ch, col_cur, col_letter, col_pct = st.columns([2, 1, 1.2, 1.5, 1.5])
            with col_code:
                st.markdown(
                    f'<div style="padding:8px 0;font-weight:bold;color:#FAFAFA">{code}</div>',
                    unsafe_allow_html=True,
                )
            with col_ch:
                st.markdown(
                    f'<div style="padding:8px 0;color:#8B9DC0">{ch} CH</div>',
                    unsafe_allow_html=True,
                )
            with col_cur:
                if lg:
                    st.markdown(
                        f'<div style="padding:8px 0;color:#A5D6A7;font-weight:bold">{lg}</div>',
                        unsafe_allow_html=True,
                    )
                else:
                    st.markdown(
                        '<div style="padding:8px 0;color:#555">—</div>',
                        unsafe_allow_html=True,
                    )
            with col_letter:
                st.text_input(
                    "Grade (A+, A, B…)", key=f"sim_letter_{code}",
                    label_visibility="collapsed", placeholder="e.g. B+",
                )
            with col_pct:
                st.number_input(
                    "Percentage", key=f"sim_pct_{code}",
                    min_value=0.0, max_value=100.0, step=1.0,
                    label_visibility="collapsed",
                    on_change=_cb_pct_to_letter, args=(code,),
                )

        hc1, hc2, hc3, hc4, hc5 = st.columns([2, 1, 1.2, 1.5, 1.5])
        with hc1: st.caption("Course")
        with hc2: st.caption("CH")
        with hc3: st.caption("Current Grade")
        with hc4: st.caption("Letter Input")
        with hc5: st.caption("% Input")

        if st.button("Calculate Projected CGPA", type="primary"):
            hypo: dict = {}
            for c in current_courses:
                code = c["course_code"]
                pct  = st.session_state.get(f"sim_pct_{code}", 0) or 0
                letter = str(st.session_state.get(f"sim_letter_{code}", "") or "").strip().upper()
                if float(pct) > 0:
                    hypo[code] = float(pct)
                elif letter in _GRADE_LETTERS:
                    hypo[code] = letter
            if hypo:
                sim = simulate_gpa(sid, hypo)
                st.session_state["sim_result"] = sim
            else:
                st.warning("Enter at least one letter grade or percentage to simulate.")

        sim_result = st.session_state.get("sim_result")
        if sim_result:
            delta = sim_result.get("cgpa_change", 0) or 0
            delta_color = "#43A047" if delta > 0 else "#E53935" if delta < 0 else "#8B9DC0"
            delta_sign  = "+" if delta > 0 else ""
            proj_cgpa   = sim_result.get("projected_cgpa")
            off_cgpa    = sim_result.get("current_official_cgpa")
            converted   = sim_result.get("converted_grades") or {}

            st.markdown(
                f'<div class="sim-result">'
                f'<div style="font-size:0.8em;color:#6B7EA0;margin-bottom:8px">SIMULATION RESULT</div>'
                f'<div style="display:flex;gap:32px;align-items:flex-end;margin-bottom:12px">'
                f'<div><div style="font-size:0.75em;color:#8B9DC0">Official CGPA</div>'
                f'<div style="font-size:1.5em;font-weight:bold;color:#FAFAFA">'
                f'{_fmt_cgpa(off_cgpa)}</div></div>'
                f'<div><div style="font-size:0.75em;color:#8B9DC0">Change from current CGPA</div>'
                f'<div style="font-size:1.8em;font-weight:bold;color:{delta_color}">'
                f'{delta_sign}{delta:.2f}</div></div>'
                f'</div>',
                unsafe_allow_html=True,
            )
            if converted:
                conv_str = "  ·  ".join(f"{c}: {l}" for c, l in converted.items())
                st.markdown(
                    f'<div style="font-size:0.82em;color:#8B9DC0;margin-bottom:10px">'
                    f'Percentage conversions: {conv_str}</div>',
                    unsafe_allow_html=True,
                )

            breakdown = sim_result.get("per_course_breakdown") or []
            shown = [c for c in breakdown if c["course_code"] in
                     {cc["course_code"] for cc in current_courses}]
            if shown:
                st.markdown(
                    '<div style="font-size:0.82em;color:#8B9DC0;margin-bottom:4px">'
                    'Per-course contribution:</div>',
                    unsafe_allow_html=True,
                )
                for entry in shown:
                    st.markdown(
                        f'<div style="display:flex;justify-content:space-between;'
                        f'font-size:0.85em;padding:3px 0;border-bottom:1px solid #2D3250">'
                        f'<span style="color:#FAFAFA">{entry["course_code"]}</span>'
                        f'<span style="color:#A5D6A7">{entry["grade"]}</span>'
                        f'<span style="color:#8B9DC0">{entry["credit_hours"]} CH</span>'
                        f'<span style="color:#FAFAFA">'
                        f'{entry["grade_points"]} × {entry["credit_hours"]} = '
                        f'{entry["contribution"]:.1f} CP</span>'
                        f'</div>',
                        unsafe_allow_html=True,
                    )
            st.markdown("</div>", unsafe_allow_html=True)

    # ── Section 5: Your Priority Right Now ───────────────────────────────────
    suggestions = result.get("suggestions") or []
    real_suggestions = [
        s for s in suggestions
        if "keep up" not in s.lower() and "good academic track" not in s.lower()
    ]

    aa_for_recs = _get_advisor_analysis(sid)
    key_pts_for_recs = aa_for_recs.get("key_points") or [] if "error" not in aa_for_recs else []

    cat_perf_sv = result.get("category_performance") or {}
    cat_cats_sv = cat_perf_sv.get("categories") or {}
    rules_sv    = result.get("rules") or {}

    focus_para = _get_student_focus(
        sid,
        {"profile": profile, "official_cgpa": cgpa},
        key_pts=key_pts_for_recs,
        category_performance=cat_cats_sv,
        rules=rules_sv,
    )

    if focus_para or real_suggestions:
        st.divider()
        st.markdown("##### Your Priority Right Now")
        if focus_para:
            st.markdown(
                f'<div style="background:#1A1F35;border-radius:10px;padding:20px 24px;'
                f'color:#FAFAFA;font-size:1.02em;line-height:1.7;border:1px solid #2D3250">'
                f'{focus_para}</div>',
                unsafe_allow_html=True,
            )
            st.markdown(
                '<div style="color:#6B7EA0;font-size:0.78em;margin-top:8px;padding-left:4px">'
                'Generated by AI based on your academic record. '
                'Always discuss with your advisor.</div>',
                unsafe_allow_html=True,
            )
        else:
            high_kw = ("schedule a meeting", "retaking failed", "block graduation", "elevated risk")
            for suggestion in real_suggestions[:1]:
                css = "alh" if any(kw in suggestion.lower() for kw in high_kw) else "alw"
                st.markdown(
                    f'<div class="{css}">{suggestion}</div>',
                    unsafe_allow_html=True,
                )

    # ── Section 6: Anomaly Alerts ─────────────────────────────────────────────
    anomalies = result.get("anomalies") or {}
    if anomalies.get("flagged"):
        st.divider()
        st.markdown("##### Anomaly Alerts")
        det = anomalies.get("details", {})
        for flag in anomalies.get("flags", []):
            if flag == "repeated_course":
                courses_txt = ", ".join(
                    f"{c} ({n}×)" for c, n in det.get("repeated_courses", {}).items()
                )
                st.markdown(
                    f'<div class="alh">&#9888;&#65039; <strong>Course Repetition:</strong> '
                    f'{courses_txt} — same course attempted 3+ times.</div>',
                    unsafe_allow_html=True,
                )
            elif flag == "undercredited_senior":
                est   = det.get("estimated_phs", "N/A")
                minex = det.get("minimum_expected_phs", "N/A")
                st.markdown(
                    f'<div class="alw">&#9888;&#65039; <strong>Undercredited Senior:</strong> '
                    f'~{est} passed credit hours (min expected: {minex}). '
                    'Advisor review recommended.</div>',
                    unsafe_allow_html=True,
                )
            elif flag == "silent_decline":
                st.markdown(
                    '<div class="alw">&#9888;&#65039; <strong>Silent Decline:</strong> '
                    'CGPA trending down but no warning issued — act now.</div>',
                    unsafe_allow_html=True,
                )

    # ── Section 6.5: Performance by Subject Area ──────────────────────────────
    cat_perf_sv2 = result.get("category_performance") or {}
    categories_sv = cat_perf_sv2.get("categories") or {}

    if len(categories_sv) >= 2:
        st.divider()
        st.markdown("##### Average Grade by Subject Area")

        def _gp_color_sv(gp: float) -> str:
            if gp >= 3.4: return "#43A047"
            if gp >= 2.8: return "#29B6F6"
            if gp >= 2.0: return "#FB8C00"
            return "#E53935"

        cat_rows_sv = [
            {
                "Category": cat,
                "Avg Grade": stats.get("avg_grade_points", 0),
                "Letter":    stats.get("avg_letter_grade", "?"),
            }
            for cat, stats in categories_sv.items()
        ]
        cat_df_sv  = pd.DataFrame(cat_rows_sv)
        colors_sv  = [_gp_color_sv(r["Avg Grade"]) for _, r in cat_df_sv.iterrows()]
        fig_cat_sv = go.Figure(go.Bar(
            y=cat_df_sv["Category"],
            x=cat_df_sv["Avg Grade"],
            orientation="h",
            marker_color=colors_sv,
            text=cat_df_sv["Letter"],
            textposition="outside",
            hovertemplate="%{y}<br>Avg Grade Points: %{x:.2f}<extra></extra>",
        ))
        fig_cat_sv.update_layout(
            template="plotly_dark",
            height=max(200, len(categories_sv) * 42 + 60),
            margin=dict(l=0, r=80, t=10, b=10),
            plot_bgcolor="#1E2130", paper_bgcolor="#1E2130",
            xaxis={"range": [0, 4.5], "title": "Avg Grade Points (0–4.0)", "gridcolor": "#2D3250"},
            yaxis={"gridcolor": "#2D3250"},
        )
        st.plotly_chart(fig_cat_sv, use_container_width=True)
        strongest_sv = cat_perf_sv2.get("strongest_category")
        weakest_sv   = cat_perf_sv2.get("weakest_category")
        stat_parts_sv = []
        if strongest_sv:
            stat_parts_sv.append(
                f'<span style="color:#43A047;font-weight:bold">Strongest area: {strongest_sv}</span>'
            )
        if weakest_sv and weakest_sv != strongest_sv:
            stat_parts_sv.append(
                f'<span style="color:#E53935;font-weight:bold">Needs focus: {weakest_sv}</span>'
            )
        if stat_parts_sv:
            st.markdown(
                '<div style="display:flex;gap:24px;padding:4px 0">'
                + " &nbsp;·&nbsp; ".join(stat_parts_sv)
                + '</div>',
                unsafe_allow_html=True,
            )

    # ── Section 7: Prerequisite Bottleneck ───────────────────────────────────
    pb = result.get("prerequisite_bottleneck") or {}
    blockers_sv = pb.get("blockers") or []

    if "error" not in pb:
        st.divider()
        st.markdown("##### Recommended Retakes — Courses Blocking Your Progress")
        if not blockers_sv:
            st.markdown(
                '<div class="als">&#10003; No prerequisite blockers — all paths are open.</div>',
                unsafe_allow_html=True,
            )
        else:
            for b in blockers_sv:
                sc = "#E53935" if b["severity"] == "high" else "#FB8C00"
                st.markdown(
                    f'<div style="background:#1E2130;border-radius:8px;padding:12px 16px;'
                    f'margin:8px 0;border-left:4px solid {sc}">'
                    f'<div style="font-weight:bold;color:#FAFAFA;font-size:1.05em">'
                    f'{b["course_code"]}</div>'
                    f'<div style="color:#FFD180;font-size:0.88em;margin-top:4px">'
                    f'{b.get("priority_message", "")}</div>'
                    f'<div style="color:#8B9DC0;font-size:0.82em;margin-top:4px">'
                    f'Recommended: retake this course</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )


# ══════════════════════════════════════════════════════════════════════════════
# PAGE 2 — Advisor View
# ══════════════════════════════════════════════════════════════════════════════

def _render_student_analysis_tab(sid: str) -> None:
    """
    Phase E: Merged Student Lookup + Advisor Analysis tab.
    Renders the full student analysis layout for advisor use.
    """
    aa_data = _get_advisor_analysis(sid)
    if "error" in aa_data:
        st.markdown(
            f'<div class="alh"><strong>Not found:</strong> '
            f'No record for <code>{sid}</code>.</div>',
            unsafe_allow_html=True,
        )
        return

    res_base  = _analyze(sid)
    prof      = aa_data.get("profile") or {}
    cgpa      = aa_data.get("official_cgpa")
    rl        = aa_data.get("risk_level") or "low"
    risk_flags_aa = aa_data.get("risk_flags") or []
    key_pts   = aa_data.get("key_points") or []
    tps       = aa_data.get("talking_points") or []
    cs        = aa_data.get("cohort_stats") or {}
    creds     = prof.get("official_credits_passed")
    expected_sem = aa_data.get("expected_credits")

    # ── Row 1: Header banner ─────────────────────────────────────────────────
    st.markdown(
        f'<div class="banner">'
        f'<div style="display:flex;justify-content:space-between;align-items:flex-start">'
        f'<div>'
        f'<div style="font-size:1.6em;font-weight:bold;color:#FAFAFA">'
        f'{_na(prof.get("name"))}'
        f'<span style="color:#8B9DC0;font-size:0.6em;margin-left:10px">{sid}</span></div>'
        f'<div style="color:#8B9DC0;font-size:0.9em;margin-top:4px">'
        f'{_shorten(prof.get("program"))}</div>'
        f'<div style="color:#6B7EA0;font-size:0.82em;margin-top:2px">'
        f'{_na(prof.get("level"))} · {_na(prof.get("study_status"))} · '
        f'CGPA: <strong style="color:{_cgpa_color(cgpa)}">{_fmt_cgpa(cgpa)}</strong>'
        f'</div></div>'
        f'<div style="padding-top:4px">{_risk_badge(rl)}</div>'
        f'</div></div>',
        unsafe_allow_html=True,
    )

    # ── Row 2: Four metric cards ─────────────────────────────────────────────
    consec_w_aa = int(prof.get("consecutive_warning") or 0)
    warn_col    = "#E53935" if consec_w_aa >= 3 else "#FB8C00" if consec_w_aa > 0 else "#43A047"
    warn_str    = str(consec_w_aa) if consec_w_aa > 0 else "None"
    _cs_direct  = _get_cohort_stats(sid)
    c_pct       = _cs_direct.get("student_percentile")
    if c_pct is not None:
        if c_pct > 50:
            pct_str   = f"Top {100 - c_pct:.0f}% of students"
            pct_color = "#43A047"
        else:
            pct_str   = f"Bottom {c_pct:.0f}% of students"
            pct_color = "#E53935"
    else:
        pct_str   = "N/A"
        pct_color = "#8B9DC0"

    m1, m2, m3, m4 = st.columns(4)
    with m1:
        st.markdown(_metric_card("OFFICIAL CGPA", _fmt_cgpa(cgpa), _cgpa_color(cgpa)), unsafe_allow_html=True)
    with m2:
        st.markdown(_metric_card("CREDITS PASSED", str(int(creds)) if creds is not None else "N/A"), unsafe_allow_html=True)
    with m3:
        st.markdown(_metric_card("WARNING STATUS", warn_str, warn_col), unsafe_allow_html=True)
    with m4:
        st.markdown(_metric_card("COHORT PERCENTILE", pct_str, pct_color), unsafe_allow_html=True)

    st.divider()

    # ── Row 3: CGPA chart (full width) ───────────────────────────────────────
    st.markdown("##### CGPA Progression")
    trend_data = aa_data.get("cgpa_trend_history") or []
    graded = [t for t in trend_data if (t.get("cgpa_at_end_of_semester") or 0) > 0]

    if len(graded) >= 2:
        x_act = [t["semester_label"] for t in graded]
        y_act = [round(t["cgpa_at_end_of_semester"], 2) for t in graded]

        fig_cg = go.Figure()
        fig_cg.add_trace(go.Scatter(
            x=x_act, y=y_act,
            mode="lines+markers", name="Actual CGPA",
            line=dict(color="#29B6F6", width=2),
            marker=dict(size=7),
            hovertemplate="%{x}<br>CGPA: %{y:.2f}<extra></extra>",
        ))

        fig_cg.add_hline(
            y=2.0, line_dash="dot", line_color="#E53935", opacity=0.7,
            annotation_text="Minimum CGPA", annotation_position="bottom right",
            annotation_font_color="#E53935",
        )
        fig_cg.update_layout(
            template="plotly_dark", height=260,
            yaxis={"range": [0, 4.3], "title": "CGPA", "gridcolor": "#2D3250"},
            xaxis={"title": "", "tickangle": -35, "gridcolor": "#2D3250"},
            margin=dict(l=0, r=80, t=10, b=60),
            plot_bgcolor="#1E2130", paper_bgcolor="#1E2130",
            showlegend=False,
        )
        st.plotly_chart(fig_cg, use_container_width=True)
    else:
        st.markdown('<div class="isd">Insufficient data for CGPA chart.</div>', unsafe_allow_html=True)

    # Risk flags summary (replaces old Risk Score Breakdown)
    if risk_flags_aa:
        for rf in risk_flags_aa:
            sev_col = "#E53935" if rf.get("severity") == "critical" else "#FB8C00"
            sev_lbl = "Critical" if rf.get("severity") == "critical" else "High"
            st.markdown(
                f'<div style="background:#1E2130;border-left:4px solid {sev_col};'
                f'border-radius:6px;padding:8px 12px;margin:4px 0;font-size:0.9em">'
                f'<span style="color:{sev_col};font-weight:bold">{sev_lbl}:</span> '
                f'{rf["message"]}</div>',
                unsafe_allow_html=True,
            )

    st.divider()

    # ── Row 4: Key Points ─────────────────────────────────────────────────────
    st.markdown("#### Academic Key Points")

    issues    = [p for p in key_pts if p["severity"] != "positive"]
    strengths = [p for p in key_pts if p["severity"] == "positive"]

    if issues:
        st.markdown("**Issues**")
        for kp in issues:
            sev = kp["severity"]
            if sev == "critical":
                bg, fg = "#4A1010", "#FF8A80"
            elif sev == "high":
                bg, fg = "#4A2800", "#FFB74D"
            else:
                bg, fg = "#2D2200", "#FFD180"
            st.markdown(
                f'<div style="background:{bg};color:{fg};border-radius:8px;'
                f'padding:10px 14px;margin:4px 0;font-size:0.9em">'
                f'{kp["emoji"]} {kp["message"]}</div>',
                unsafe_allow_html=True,
            )

    if strengths:
        if issues:
            st.markdown("**Strengths**")
        for kp in strengths:
            st.markdown(
                f'<div style="background:#0A3020;color:#A5D6A7;border-radius:8px;'
                f'padding:10px 14px;margin:4px 0;font-size:0.9em">'
                f'{kp["emoji"]} {kp["message"]}</div>',
                unsafe_allow_html=True,
            )

    if not key_pts:
        st.markdown('<div class="isd">No key points generated.</div>', unsafe_allow_html=True)

    st.divider()

    # ── Row 5: Advising Session Focus + Track Performance ────────────────────
    col_adv, col_track = st.columns([1, 1])

    with col_adv:
        st.markdown("#### Advising Session Guide")

        cat_perf_adv = aa_data.get("category_performance") or {}
        cat_cats_adv = cat_perf_adv.get("categories") or {}
        session_guide = _get_advisor_session_guide(sid, aa_data, category_performance=cat_cats_adv)

        if session_guide:
            gc1, gc2, gc3 = st.columns(3)
            with gc1:
                st.markdown(
                    '<div style="background:#1A2540;border-radius:10px;padding:16px;'
                    'border-left:4px solid #4C9BE8;height:100%">'
                    '<div style="color:#4C9BE8;font-size:0.75em;font-weight:bold;'
                    'letter-spacing:0.06em;margin-bottom:10px">HOW TO OPEN</div>'
                    f'<div style="color:#FAFAFA;font-size:0.88em;line-height:1.65">'
                    f'{session_guide["opening"]}</div></div>',
                    unsafe_allow_html=True,
                )
            with gc2:
                st.markdown(
                    '<div style="background:#2D2200;border-radius:10px;padding:16px;'
                    'border-left:4px solid #FB8C00;height:100%">'
                    '<div style="color:#FB8C00;font-size:0.75em;font-weight:bold;'
                    'letter-spacing:0.06em;margin-bottom:10px">KEY QUESTION TO ASK</div>'
                    f'<div style="color:#FFD180;font-size:0.88em;line-height:1.65;'
                    f'font-style:italic">{session_guide["key_question"]}</div></div>',
                    unsafe_allow_html=True,
                )
            with gc3:
                st.markdown(
                    '<div style="background:#0A2A0A;border-radius:10px;padding:16px;'
                    'border-left:4px solid #43A047;height:100%">'
                    '<div style="color:#43A047;font-size:0.75em;font-weight:bold;'
                    'letter-spacing:0.06em;margin-bottom:10px">AIM TO AGREE ON</div>'
                    f'<div style="color:#A5D6A7;font-size:0.88em;line-height:1.65">'
                    f'{session_guide["session_goal"]}</div></div>',
                    unsafe_allow_html=True,
                )
            st.caption("AI-generated conversation guide — use your professional judgment")
        else:
            for i, tp in enumerate(tps[:3], 1):
                st.markdown(
                    f'<div style="background:#1A1F35;border-radius:8px;padding:12px 16px;'
                    f'margin:6px 0;border-left:3px solid #4C9BE8;display:flex;'
                    f'align-items:flex-start;gap:12px">'
                    f'<span style="background:#4C9BE8;color:#0E1117;font-weight:bold;'
                    f'border-radius:50%;min-width:24px;height:24px;display:inline-flex;'
                    f'align-items:center;justify-content:center;font-size:0.85em">{i}</span>'
                    f'<span style="color:#FAFAFA;font-size:0.9em">{tp}</span>'
                    f'</div>',
                    unsafe_allow_html=True,
                )

    with col_track:
        st.markdown("#### Performance by Subject Area")
        cat_perf_tab = aa_data.get("category_performance") or {}
        categories_tab = cat_perf_tab.get("categories") or {}

        if len(categories_tab) >= 2:
            def _gp_color_tab(gp: float) -> str:
                if gp >= 3.4: return "#43A047"
                if gp >= 2.8: return "#29B6F6"
                if gp >= 2.0: return "#FB8C00"
                return "#E53935"

            cat_rows_tab = [
                {
                    "Category": cat,
                    "Avg Grade": stats.get("avg_grade_points", 0),
                    "Letter":    stats.get("avg_letter_grade", "?"),
                }
                for cat, stats in categories_tab.items()
            ]
            cat_df_tab  = pd.DataFrame(cat_rows_tab)
            colors_tab  = [_gp_color_tab(r["Avg Grade"]) for _, r in cat_df_tab.iterrows()]
            fig_cat_tab = go.Figure(go.Bar(
                y=cat_df_tab["Category"],
                x=cat_df_tab["Avg Grade"],
                orientation="h",
                marker_color=colors_tab,
                text=cat_df_tab["Letter"],
                textposition="outside",
                hovertemplate="%{y}<br>Avg Grade Points: %{x:.2f}<extra></extra>",
            ))
            fig_cat_tab.update_layout(
                template="plotly_dark",
                height=max(220, len(categories_tab) * 42 + 60),
                margin=dict(l=0, r=80, t=10, b=10),
                plot_bgcolor="#1E2130", paper_bgcolor="#1E2130",
                xaxis={"range": [0, 4.5], "title": "Avg Grade Points (0–4.0)", "gridcolor": "#2D3250"},
                yaxis={"gridcolor": "#2D3250"},
            )
            st.plotly_chart(fig_cat_tab, use_container_width=True)

            def _status_color_tab(series: pd.Series) -> list[str]:
                out = []
                for v in series:
                    vl = str(v).lower()
                    if vl == "strong":
                        out.append("background-color:#0A3020;color:#A5D6A7")
                    elif vl == "adequate":
                        out.append("background-color:#0D2A2A;color:#80CBC4")
                    elif vl == "struggling":
                        out.append("background-color:#4A3000;color:#FFD180")
                    elif vl == "critical":
                        out.append("background-color:#4A1010;color:#FF8A80")
                    else:
                        out.append("")
                return out

            tbl_rows_tab = []
            for cat, stats in categories_tab.items():
                tbl_rows_tab.append({
                    "Category":  cat,
                    "Attempted": stats["courses_attempted"],
                    "Passed":    stats["courses_passed"],
                    "Failed":    stats["courses_failed"],
                    "Avg Grade": stats.get("avg_letter_grade", "?"),
                    "Status":    stats["status"].title(),
                })
            cat_tbl_df = pd.DataFrame(tbl_rows_tab)
            st.dataframe(
                cat_tbl_df.style.apply(_status_color_tab, subset=["Status"]),
                use_container_width=True, hide_index=True,
                height=min(300, len(cat_tbl_df) * 36 + 40),
            )

            strongest_tab = cat_perf_tab.get("strongest_category")
            weakest_tab   = cat_perf_tab.get("weakest_category")
            if strongest_tab:
                st.markdown(
                    f'<div style="background:#0A3020;color:#A5D6A7;border-radius:6px;'
                    f'padding:5px 10px;font-size:0.82em;margin:2px 0">'
                    f'Strongest: {strongest_tab}</div>',
                    unsafe_allow_html=True,
                )
            if weakest_tab and weakest_tab != strongest_tab:
                st.markdown(
                    f'<div style="background:#4A1010;color:#FF8A80;border-radius:6px;'
                    f'padding:5px 10px;font-size:0.82em;margin:2px 0">'
                    f'Needs focus: {weakest_tab}</div>',
                    unsafe_allow_html=True,
                )
        else:
            st.markdown(
                '<div class="isd">No subject area data (need at least 2 categories with 2+ courses each).</div>',
                unsafe_allow_html=True,
            )

    # ── Row 6: Prerequisite Bottleneck ────────────────────────────────────────
    pb = aa_data.get("prerequisite_bottleneck") or {}
    bl = pb.get("blockers") or []
    active_bl = [b for b in bl if b.get("unlock_count", 0) > 0]
    if "error" not in pb:
        st.divider()
        st.markdown("#### Recommended Retakes — Courses Blocking Your Progress")
        if not active_bl:
            st.markdown(
                '<div class="als">&#10003; No prerequisite blockers — all paths are open.</div>',
                unsafe_allow_html=True,
            )
        else:
            for b in active_bl:
                sc = "#E53935" if b["severity"] == "high" else "#FB8C00"
                st.markdown(
                    f'<div style="background:#1E2130;border-radius:8px;padding:10px 14px;'
                    f'margin:6px 0;border-left:4px solid {sc}">'
                    f'<div style="font-weight:bold;color:#FAFAFA;font-size:1.02em">{b["course_code"]}</div>'
                    f'<div style="color:#FFD180;font-size:0.88em;margin-top:4px">'
                    f'{b.get("priority_message", "")}</div>'
                    f'<div style="color:#8B9DC0;font-size:0.82em;margin-top:4px">'
                    f'Recommended: retake this course</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )

    # ── Row 7: Semester Difficulty Index ──────────────────────────────────────
    sdi = aa_data.get("semester_difficulty") or {}
    if "error" not in sdi and sdi.get("course_count", 0) > 0:
        st.divider()
        st.markdown("#### Semester Difficulty Index")
        sdi_score = sdi.get("sdi_score", 0)
        sdi_col   = "#43A047" if sdi_score < 1.8 else "#FB8C00" if sdi_score <= 2.4 else "#E53935"
        st.markdown(
            f'<div class="card-box">'
            f'<div style="font-size:2em;font-weight:bold;color:{sdi_col};margin-bottom:4px">'
            f'{sdi_score:.2f} / 3.00</div>'
            f'<div style="color:#8B9DC0;font-size:0.88em">'
            f'{sdi.get("flag", "OK")} — {sdi.get("flag_message", "")}</div>'
            f'<div style="color:#6B7EA0;font-size:0.8em;margin-top:6px">'
            f'High-risk courses: {sdi.get("high_risk_count", 0)} · '
            f'Medium: {sdi.get("medium_risk_count", 0)} · '
            f'Low: {sdi.get("low_risk_count", 0)}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )
        st.caption(
            "Score = average difficulty of your registered courses. "
            "High-risk course = 3 pts, Medium = 2, Low = 1. "
            "Scale: 1.0 (easy load) → 3.0 (very difficult load). "
            "Courses with no graded history are excluded from the score."
        )
        sdi_courses = sdi.get("course_list") or []
        if sdi_courses:
            _SDI_PTS = {"high": 3, "medium": 2, "low": 1, "unknown": 2}
            sdi_rows = []
            for _sc in sdi_courses:
                _pr   = _sc.get("pass_rate_pct")
                _cat  = (_sc.get("risk_category") or "unknown").lower()
                _pts  = _SDI_PTS.get(_cat, "—") if _cat not in ("insufficient_data",) else "—"
                sdi_rows.append({
                    "Course Code":          _sc["course_code"],
                    "Historical Pass Rate":  f"{_pr:.1f}%" if _pr is not None else "N/A",
                    "Risk Category":         _cat.replace("_", " ").title(),
                    "Risk Score":            _pts,
                })
            sdi_df = pd.DataFrame(sdi_rows)

            def _sdi_col(series: pd.Series) -> list[str]:
                out = []
                for v in series:
                    vl = str(v).lower()
                    if vl == "high":
                        out.append("background-color:#4A1010;color:#FF8A80")
                    elif vl == "medium":
                        out.append("background-color:#4A3000;color:#FFD180")
                    elif vl == "low":
                        out.append("background-color:#0A3020;color:#A5D6A7")
                    else:
                        out.append("color:#8B9DC0")
                return out

            st.dataframe(
                sdi_df.style.apply(_sdi_col, subset=["Risk Category"]),
                use_container_width=True, hide_index=True,
                height=min(300, len(sdi_df) * 36 + 40),
            )

    # ── Row 8: Registration History ───────────────────────────────────────────
    st.divider()
    st.markdown("#### Registration History")
    regs_list = aa_data.get("registrations") or []
    if regs_list:
        reg_rows = []
        for reg in regs_list:
            grade  = reg.get("letter_grade") or ""
            status = reg.get("registration_status") or ""
            is_retake = "repeat" in status.lower() or "improve" in status.lower()
            reg_rows.append({
                "Semester":    reg.get("semester", ""),
                "Course Code": reg.get("course_code", ""),
                "Grade":       grade,
                "Type":        "Retake" if is_retake else "First Attempt",
            })
        reg_df = pd.DataFrame(reg_rows).sort_values("Semester", ascending=True).reset_index(drop=True)
        st.caption(f"{len(reg_df)} registration records")

        def _color_grade_hist(series: pd.Series) -> list[str]:
            out = []
            for v in series:
                g = str(v or "").strip().upper()
                if g in ("A+", "A", "A-"):
                    out.append("background-color:#0A3020;color:#A5D6A7;font-weight:bold")
                elif g in ("B+", "B", "B-"):
                    out.append("background-color:#0D2A0D;color:#81C784;font-weight:bold")
                elif g in ("C+", "C", "C-"):
                    out.append("background-color:#2D2200;color:#FFD180;font-weight:bold")
                elif g in ("D+", "D", "D-"):
                    out.append("background-color:#2D1500;color:#FFAB40;font-weight:bold")
                elif g == "F":
                    out.append("background-color:#4A1010;color:#FF8A80;font-weight:bold")
                else:
                    out.append("color:#8B9DC0")
            return out

        st.dataframe(
            reg_df.style.apply(_color_grade_hist, subset=["Grade"]),
            use_container_width=True, hide_index=True,
            height=min(600, len(reg_df) * 36 + 40),
        )
    else:
        st.markdown('<div class="isd">No registration history found.</div>', unsafe_allow_html=True)


def page_advisor_view() -> None:
    overview_all = _load_overview()

    _ALL_ADVISORS = [f"ADV{str(i + 1).zfill(3)}" for i in range(10)]
    selected_advisor = st.selectbox("Select Advisor", _ALL_ADVISORS, key="selected_advisor")
    overview = [s for s in overview_all if _advisor_for_student(s.get("id", "")) == selected_advisor]
    st.caption(f"Showing {len(overview)} students assigned to {selected_advisor}")
    st.divider()

    n_total = len(overview)
    n_high  = sum(1 for s in overview if s.get("risk_level") == "high")
    n_mod   = sum(1 for s in overview if s.get("risk_level") == "moderate")
    n_low   = sum(1 for s in overview if s.get("risk_level") == "low")

    # ── Summary cards ──────────────────────────────────────────────────────────
    sc1, sc2, sc3, sc4 = st.columns(4)
    with sc1:
        st.markdown(_metric_card("TOTAL ACTIVE", str(n_total)), unsafe_allow_html=True)
    with sc2:
        st.markdown(_metric_card("AT RISK", str(n_high), "#FF8A80"), unsafe_allow_html=True)
    with sc3:
        st.markdown(_metric_card("NEEDS ATTENTION", str(n_mod), "#FFD180"), unsafe_allow_html=True)
    with sc4:
        st.markdown(_metric_card("ON TRACK", str(n_low), "#A5D6A7"), unsafe_allow_html=True)

    st.divider()

    # ── Immediate Actions ──────────────────────────────────────────────────────
    imm = [s for s in overview if s.get("immediate_action")]

    def _imm_sort_key(item: dict) -> tuple:
        reason = item.get("immediate_reason") or ""
        consec = item.get("consecutive_warning") or 0
        cgpa   = item.get("official_cgpa") or 4.0
        if "Active academic warning" in reason:
            return (0, -consec, cgpa)
        if "CGPA below minimum" in reason:
            return (1, -consec, cgpa)
        if "Repeated course failure" in reason:
            return (2, -consec, cgpa)
        return (3, -consec, cgpa)

    imm.sort(key=_imm_sort_key)

    st.markdown("#### ⚡ Immediate Actions Required")
    if not imm:
        st.markdown(
            '<div class="als">&#10003; No students require immediate action.</div>',
            unsafe_allow_html=True,
        )
    else:
        for item in imm:
            cgpa_f   = item.get("official_cgpa")
            cgpa_str = _fmt_cgpa(cgpa_f)
            cgpa_col = _cgpa_color(cgpa_f)
            reason   = item.get("immediate_reason") or "Requires attention"

            col_card, col_btn = st.columns([6, 1])
            with col_card:
                st.markdown(
                    f'<div class="atc">'
                    f'<div style="display:flex;justify-content:space-between;align-items:center">'
                    f'<div>'
                    f'<span style="font-weight:bold;color:#FAFAFA;font-size:1.04em">'
                    f'{_na(item.get("name"))}</span>'
                    f'<span style="color:#8B9DC0;font-size:0.85em;margin-left:6px">'
                    f'({_na(item.get("id"))})</span>'
                    f'<span style="background:#1E2540;color:#8B9DC0;padding:2px 8px;'
                    f'border-radius:4px;font-size:0.78em;margin-left:10px">'
                    f'{_na(item.get("level"))}</span>'
                    f'</div>'
                    f'<span style="color:{cgpa_col};font-weight:bold">CGPA {cgpa_str}</span>'
                    f'</div>'
                    f'<div style="color:#FF8A80;font-size:0.85em;margin-top:6px;font-weight:bold">'
                    f'&#9888; {reason}</div>'
                    f'<div style="color:#8B9DC0;font-size:0.8em;margin-top:3px">'
                    f'{_shorten(item.get("program"))}</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
            with col_btn:
                if st.button("View Profile", key=f"imm_btn_{item['id']}"):
                    st.session_state["lookup_student_id"] = item["id"]
                    st.session_state["advisor_main_tab"] = "Student Analysis"
                    st.session_state["current_page"] = "Advisor View"
                    st.rerun()

    st.divider()

    # ── Tabs: All Students | Course Risk Index | Student Analysis ─────────────
    advisor_tab = st.radio(
        "Section",
        ["All Students", "Course Risk Index", "Student Analysis"],
        horizontal=True,
        key="advisor_main_tab",
        label_visibility="collapsed",
    )

    # ── Tab: All Students ─────────────────────────────────────────────────────
    if advisor_tab == "All Students":
        fc1, fc2, fc3, fc4 = st.columns(4)
        with fc1:
            levels = ["All"] + sorted({s.get("level", "") or "" for s in overview} - {""})
            sel_level = st.selectbox("Level", levels, key="adv_level")
        with fc2:
            sel_risk = st.selectbox("Risk Level", ["All", "high", "moderate", "low"], key="adv_risk")
        with fc3:
            progs_raw = sorted({_shorten(s.get("program")) for s in overview} - {"N/A", ""})
            sel_prog  = st.selectbox("Program", ["All"] + progs_raw, key="adv_prog")
        with fc4:
            only_imm = st.checkbox("Immediate Action Only", key="adv_imm_only")

        filtered = list(overview)
        if sel_level != "All":
            filtered = [s for s in filtered if s.get("level") == sel_level]
        if sel_risk != "All":
            filtered = [s for s in filtered if s.get("risk_level") == sel_risk]
        if sel_prog != "All":
            filtered = [s for s in filtered if _shorten(s.get("program")) == sel_prog]
        if only_imm:
            filtered = [s for s in filtered if s.get("immediate_action")]

        st.caption(f"Showing **{len(filtered)}** of **{n_total}** students")

        rows = []
        for s in filtered:
            cgpa_val = s.get("official_cgpa")
            rows.append({
                "ID":               s.get("id", ""),
                "Name":             s.get("name", ""),
                "Level":            s.get("level", ""),
                "Program":          _shorten(s.get("program", "")),
                "CGPA":             _fmt_cgpa(cgpa_val),
                "Credits":          int(s["credits_completed"]) if s.get("credits_completed") is not None else "—",
                "Consec. Warnings": s.get("consecutive_warning") or 0,
                "Risk Level":       (s.get("risk_level") or "").capitalize(),
                "Immediate":        "🔴" if s.get("immediate_action") else "—",
            })

        if rows:
            df_show = pd.DataFrame(rows)

            def _color_risk(series: pd.Series) -> list[str]:
                out = []
                for v in series:
                    vl = str(v).lower()
                    if vl == "high":
                        out.append("background-color:#4A1010;color:#FF8A80;font-weight:bold")
                    elif vl == "moderate":
                        out.append("background-color:#4A3000;color:#FFD180;font-weight:bold")
                    elif vl == "low":
                        out.append("background-color:#0A3020;color:#A5D6A7;font-weight:bold")
                    else:
                        out.append("")
                return out

            def _color_cgpa_col(series: pd.Series) -> list[str]:
                out = []
                for v in series:
                    try:
                        f = float(v)
                        if f < 2.0:
                            out.append("color:#FF8A80;font-weight:bold")
                        elif f < 2.5:
                            out.append("color:#FFD180;font-weight:bold")
                        else:
                            out.append("color:#A5D6A7")
                    except (ValueError, TypeError):
                        out.append("color:#8B9DC0")
                return out

            def _color_warn(series: pd.Series) -> list[str]:
                return [
                    "color:#FF8A80;font-weight:bold" if int(v) > 0 else "color:#6B7EA0"
                    for v in series
                ]

            styled = (
                df_show.style
                .apply(_color_risk,     subset=["Risk Level"])
                .apply(_color_cgpa_col, subset=["CGPA"])
                .apply(_color_warn,     subset=["Consec. Warnings"])
            )
            st.dataframe(styled, use_container_width=True, hide_index=True, height=500)
        else:
            st.info("No students match the current filters.")

    # ── Tab: Course Risk Index ────────────────────────────────────────────────
    elif advisor_tab == "Course Risk Index":
        level_opts = ["All", "Freshman", "Sophomore", "Junior", "Senior"]
        sel_cri = st.selectbox("Filter by level", level_opts, key="cri_level")

        course_risks = _get_course_risk_cached(None if sel_cri == "All" else sel_cri)

        data_courses = [c for c in (course_risks or []) if c.get("risk_category") != "insufficient_data"]
        insuf_courses = [c for c in (course_risks or []) if c.get("risk_category") == "insufficient_data"]

        if not data_courses:
            st.info("Not enough data for the selected filter.")
        else:
            cri_df = pd.DataFrame(data_courses)

            fig_cri = px.bar(
                cri_df,
                x="pass_rate_pct", y="course_code",
                orientation="h",
                color="risk_category",
                color_discrete_map={
                    "high": _HEX["high"], "medium": _HEX["moderate"], "low": _HEX["low"]
                },
                text="pass_rate_pct",
                labels={
                    "pass_rate_pct": "Pass Rate (%)",
                    "course_code": "Course",
                    "risk_category": "Risk",
                },
                category_orders={"risk_category": ["high", "medium", "low"]},
                template="plotly_dark",
            )
            # Show "pass_rate% · AvgGrade" as bar label
            cri_df["_bar_label"] = cri_df.apply(
                lambda r: f'{r["pass_rate_pct"]:.1f}%  {r["avg_letter_grade"]}' if r.get("avg_letter_grade") else f'{r["pass_rate_pct"]:.1f}%',
                axis=1,
            )
            fig_cri.update_traces(
                text=cri_df["_bar_label"],
                texttemplate="%{text}",
                textposition="outside",
            )

            # Add grey bars for insufficient_data courses at x=0 with "?" annotation
            for ic in insuf_courses:
                fig_cri.add_trace(go.Bar(
                    x=[0],
                    y=[ic["course_code"]],
                    orientation="h",
                    marker_color="#4A5068",
                    showlegend=False,
                    text=["?"],
                    textposition="outside",
                    hovertemplate=f"{ic['course_code']}<br>Insufficient data<extra></extra>",
                ))

            total_bars = len(data_courses) + len(insuf_courses)
            fig_cri.update_layout(
                height=max(300, total_bars * 28),
                margin=dict(l=0, r=80, t=10, b=10),
                plot_bgcolor="#1E2130", paper_bgcolor="#1E2130",
                yaxis={"categoryorder": "total ascending"},
                xaxis={"range": [0, 115]},
                legend_title="Risk",
            )
            st.plotly_chart(fig_cri, use_container_width=True)

        if course_risks:
            all_for_table = data_courses + insuf_courses
            tbl = pd.DataFrame([{
                "Course Code":      c["course_code"],
                "Course Name":      c.get("course_name") or "—",
                "Pass Rate %":      f'{c["pass_rate_pct"]:.1f}%' if c.get("pass_rate_pct") is not None else "?",
                "Avg Grade Points": f'{c["avg_grade_points"]:.2f}' if c.get("avg_grade_points") is not None else "—",
                "Avg Letter Grade": c.get("avg_letter_grade") or "—",
                "Risk Category":    c["risk_category"].replace("_", " ").title(),
                "Total Attempts":   c["total_attempts"],
            } for c in all_for_table])

            def _cri_color_row(row: pd.Series) -> list[str]:
                styles = []
                for col, v in row.items():
                    vl = str(v).lower()
                    if col == "Risk Category":
                        if vl == "high":
                            styles.append("background-color:#4A1010;color:#FF8A80")
                        elif vl == "medium":
                            styles.append("background-color:#4A3000;color:#FFD180")
                        elif vl == "insufficient data":
                            styles.append("color:#6B7EA0")
                        else:
                            styles.append("background-color:#0A3020;color:#A5D6A7")
                    elif col == "Avg Letter Grade" and v != "—":
                        try:
                            gp = float(row.get("Avg Grade Points", 0) or 0)
                        except (TypeError, ValueError):
                            gp = 0.0
                        if gp >= 3.4:
                            styles.append("color:#A5D6A7;font-weight:bold")   # green — A range
                        elif gp >= 2.8:
                            styles.append("color:#64B5F6;font-weight:bold")   # blue  — B range
                        elif gp >= 2.0:
                            styles.append("color:#FFD180;font-weight:bold")   # orange — C range
                        else:
                            styles.append("color:#FF8A80;font-weight:bold")   # red — D/F range
                    else:
                        styles.append("")
                return styles

            st.dataframe(
                tbl.style.apply(_cri_color_row, axis=1),
                use_container_width=True, hide_index=True,
                height=min(500, len(tbl) * 36 + 40),
            )
            n_h = sum(1 for c in data_courses if c["risk_category"] == "high")
            n_m = sum(1 for c in data_courses if c["risk_category"] == "medium")
            st.caption(
                f"{len(data_courses)} courses with sufficient data · {n_h} below 50% pass rate · "
                f"{n_m} between 50–74% · {len(insuf_courses)} courses excluded (< 10 first attempts)"
            )

    # ── Tab: Student Analysis (Phase E — merged) ──────────────────────────────
    else:
        # Support prefill from "View in Lookup" buttons
        _sa_prefill = (
            st.session_state.pop("lookup_student_id", "")
            or st.session_state.pop("adv_lookup_prefill", "")
            or st.session_state.pop("adv_analysis_prefill", "")
        )
        if _sa_prefill:
            st.session_state.pop("adv_sa_id", None)

        sa_sid = st.text_input(
            "Student ID for Analysis",
            placeholder="e.g. STU000026",
            key="adv_sa_id",
            value=_sa_prefill if _sa_prefill else "",
        ).strip()

        if not sa_sid and _sa_prefill:
            sa_sid = _sa_prefill

        if not sa_sid:
            st.info("Enter a Student ID to load the full student analysis.")
        else:
            _render_student_analysis_tab(sa_sid)


# ══════════════════════════════════════════════════════════════════════════════
# Navigation + Main
# ══════════════════════════════════════════════════════════════════════════════

def main() -> None:
    if "current_page" not in st.session_state:
        st.session_state["current_page"] = "Student View"

    with st.sidebar:
        st.markdown("## SAE Dashboard")
        st.markdown("*Student Analysis Engine*")
        st.divider()
        _nav = st.session_state["current_page"]
        if st.button(
            "Student View",
            use_container_width=True,
            type="primary" if _nav == "Student View" else "secondary",
        ):
            st.session_state["current_page"] = "Student View"
            st.rerun()
        if st.button(
            "Advisor View",
            use_container_width=True,
            type="primary" if _nav == "Advisor View" else "secondary",
        ):
            st.session_state["current_page"] = "Advisor View"
            st.rerun()
        st.divider()
        st.caption("CGPA values shown are official records from the student information system.")

    if st.session_state["current_page"] == "Student View":
        st.title("Student Profile")
        page_student_view()
    else:
        st.title("Advisor Overview")
        page_advisor_view()


main()
