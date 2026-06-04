"""
IFC Prospect Lookup — v2 (redesigned UI)
=========================================
Same logic as app.py. Visual overhaul:
  - Wide layout with sidebar for search + step tracker
  - Custom CSS: card containers, IFC navy/green palette, proper typography
  - Signal breakdown rendered as styled cards instead of st.metric
  - Header strip with IFC branding
Run: streamlit run app_v2.py
"""

import json
import time
import base64
import os
from datetime import date, datetime
from pathlib import Path

import streamlit as st

from utils.auth import require_auth
from utils.fetchers import (
    fetch_candidates, fetch_gleif, fetch_news, fetch_sanctions,
    fetch_financials, fetch_ch_company_profile,
    extract_uploaded_pdf,
    company_age_years, country_risk_tier,
    COUNTRY_NAMES, FINANCIAL_SOURCE_URLS,
)
from utils.report import build_pdf
from concurrent.futures import ThreadPoolExecutor, as_completed

st.set_page_config(
    page_title="IFC Prospect Lookup",
    page_icon="🔍",
    layout="wide",
    initial_sidebar_state="expanded",
)
require_auth()

# ─────────────────────────────────────────────────────────────
# STYLING
# ─────────────────────────────────────────────────────────────

def _logo_b64():
    """Return base64-encoded dark-background logo for sidebar."""
    for fname in ("logo_dark.svg", "ifc_logo_dark.svg", "ifc_logo.png", "ifc_logo.svg", "logo.png", "logo.svg"):
        p = Path(__file__).parent / fname
        if p.exists():
            mime = "image/svg+xml" if fname.endswith(".svg") else "image/png"
            data = base64.b64encode(p.read_bytes()).decode()
            return f"data:{mime};base64,{data}"
    return None

LOGO_SRC = _logo_b64()

CSS = """
<style>
/* ── Reset sidebar padding ── */
[data-testid="stSidebar"] {
    background: #0d2240;
    padding-top: 0 !important;
}
[data-testid="stSidebar"] > div:first-child {
    padding-top: 0 !important;
}

/* ── Sidebar header strip ── */
.sidebar-header {
    background: #0d2240;
    padding: 24px 20px 18px 20px;
    border-bottom: 2px solid #2a6b3c;
    margin-bottom: 20px;
}
.sidebar-header img {
    max-width: 160px;
    display: block;
    margin-bottom: 8px;
}
.sidebar-header .app-title {
    font-family: 'Georgia', serif;
    font-size: 11px;
    font-weight: 600;
    color: #a8c4d4;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    margin-top: 4px;
}

/* ── Step tracker ── */
.step-list {
    list-style: none;
    padding: 0 20px;
    margin: 0 0 24px 0;
}
.step-list li {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 8px 0;
    font-size: 13px;
    color: #7a9ab5;
    border-left: 2px solid #1e3a5f;
    padding-left: 14px;
    margin-left: 8px;
}
.step-list li.active {
    color: #e8f4f0;
    border-left-color: #2a6b3c;
    font-weight: 600;
}
.step-list li.done {
    color: #4aaa6e;
    border-left-color: #2a6b3c;
}
.step-num {
    width: 22px;
    height: 22px;
    border-radius: 50%;
    background: #1e3a5f;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 11px;
    font-weight: 700;
    flex-shrink: 0;
    color: #7a9ab5;
}
.step-list li.active .step-num {
    background: #2a6b3c;
    color: #fff;
}
.step-list li.done .step-num {
    background: #1e4d2b;
    color: #4aaa6e;
}

/* ── Sidebar section label ── */
.sidebar-section {
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    color: #4a7a9b;
    padding: 0 20px;
    margin-bottom: 8px;
}

/* ── Sidebar input labels ── */
[data-testid="stSidebar"] label {
    color: #a8c4d4 !important;
    font-size: 12px !important;
}
[data-testid="stSidebar"] .stTextInput input,
[data-testid="stSidebar"] .stSelectbox > div > div {
    background: #1a3050 !important;
    border-color: #2a4a6b !important;
    color: #e8f0f8 !important;
}

/* ── Main content area ── */
.main-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 18px 0 16px 0;
    border-bottom: 1px solid #e2e6ea;
    margin-bottom: 24px;
}
.main-header h1 {
    font-family: 'Georgia', serif;
    font-size: 22px;
    font-weight: 700;
    color: #0d2240;
    margin: 0;
}
.main-header .subtitle {
    font-size: 13px;
    color: #6c7a89;
    margin-top: 2px;
}
.lookup-counter {
    font-size: 12px;
    color: #9aaab8;
    background: #f0f3f7;
    padding: 4px 10px;
    border-radius: 12px;
}

/* ── Card wrapper ── */
.ifc-card {
    background: #ffffff;
    border: 1px solid #e2e6ea;
    border-radius: 8px;
    padding: 20px 24px;
    margin-bottom: 16px;
    box-shadow: 0 1px 3px rgba(13,34,64,0.06);
}
.ifc-card h3, .ifc-card h4 {
    font-family: 'Georgia', serif;
    color: #0d2240;
    margin-top: 0;
}
.card-label {
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    color: #9aaab8;
    margin-bottom: 4px;
}

/* ── Verdict banner ── */
.verdict-proceed {
    background: linear-gradient(135deg, #1e4d2b 0%, #2a6b3c 100%);
    color: #fff;
    border-radius: 8px;
    padding: 20px 24px;
    margin-bottom: 16px;
}
.verdict-caution {
    background: linear-gradient(135deg, #5c4200 0%, #8a6200 100%);
    color: #fff;
    border-radius: 8px;
    padding: 20px 24px;
    margin-bottom: 16px;
}
.verdict-stop {
    background: linear-gradient(135deg, #5c1010 0%, #8a1818 100%);
    color: #fff;
    border-radius: 8px;
    padding: 20px 24px;
    margin-bottom: 16px;
}
.verdict-title {
    font-family: 'Georgia', serif;
    font-size: 20px;
    font-weight: 700;
    margin: 0 0 4px 0;
}
.verdict-meta {
    font-size: 12px;
    opacity: 0.75;
    margin: 0;
}

/* ── Score ring (text only, CSS fake-ring) ── */
.score-display {
    font-family: 'Georgia', serif;
    font-size: 48px;
    font-weight: 700;
    line-height: 1;
    color: #fff;
    margin-bottom: 2px;
}
.score-max {
    font-size: 14px;
    opacity: 0.6;
}

/* ── Signal cards ── */
.signal-grid {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 12px;
    margin-bottom: 16px;
}
.signal-card {
    background: #f8f9fb;
    border: 1px solid #e2e6ea;
    border-radius: 6px;
    padding: 14px 16px;
    border-left: 3px solid #ddd;
}
.signal-card.green  { border-left-color: #2a6b3c; }
.signal-card.yellow { border-left-color: #c48f00; }
.signal-card.red    { border-left-color: #8a1818; }
.signal-factor {
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: #9aaab8;
    margin-bottom: 4px;
}
.signal-label {
    font-size: 14px;
    font-weight: 600;
    color: #0d2240;
    margin-bottom: 2px;
}
.signal-pts {
    font-size: 11px;
    color: #9aaab8;
}

/* ── Company detail table ── */
.detail-row {
    display: flex;
    gap: 8px;
    padding: 6px 0;
    border-bottom: 1px solid #f0f3f7;
    font-size: 13px;
}
.detail-row:last-child { border-bottom: none; }
.detail-key {
    color: #9aaab8;
    font-weight: 600;
    width: 130px;
    flex-shrink: 0;
    font-size: 12px;
}
.detail-val { color: #0d2240; }

/* ── News items ── */
.news-item {
    padding: 8px 0;
    border-bottom: 1px solid #f0f3f7;
    font-size: 13px;
}
.news-item:last-child { border-bottom: none; }

/* ── Divider replacement ── */
.section-gap { margin: 20px 0; }

/* ── Hide default streamlit top padding ── */
.block-container { padding-top: 0 !important; }

/* ── Export buttons ── */
.stDownloadButton button {
    border-radius: 6px !important;
    font-size: 13px !important;
}
</style>
"""

st.markdown(CSS, unsafe_allow_html=True)

# ─────────────────────────────────────────────────────────────
# SCORING  (identical to app.py — do not modify)
# ─────────────────────────────────────────────────────────────

MAX_LOOKUPS = 20
_DEFAULTS = [
    ("lookup_count", 0), ("candidates", None), ("selected_co", None),
    ("analysis_data", None), ("revenue", 0), ("net_result", 0),
    ("fin_year", date.today().year - 1), ("financials_applied", False),
]
for k, v in _DEFAULTS:
    if k not in st.session_state:
        st.session_state[k] = v


def revenue_pts(rev):
    if not rev:                        return 0,  "Not entered"
    if rev >= 500_000_000: return 40, f"€{rev/1e9:.1f}B"
    if rev >= 100_000_000: return 35, f"€{rev/1e6:.0f}M"
    if rev >= 25_000_000:  return 28, f"€{rev/1e6:.1f}M"
    if rev >= 5_000_000:   return 20, f"€{rev/1e6:.1f}M"
    if rev >= 1_000_000:   return 12, f"€{rev/1e6:.2f}M"
    if rev > 0:            return  6, f"€{rev:,.0f}"
    return 0, "€0"


def net_result_pts(net, rev):
    if not rev:
        if net > 0:  return 10, f"€{net:,.0f} (positive)"
        if net < 0:  return  2, f"€{net:,.0f} (loss)"
        return 0, "Not entered"
    margin = net / rev
    if margin >= 0.15: return 20, f"{margin:.0%} margin"
    if margin >= 0.08: return 16, f"{margin:.0%} margin"
    if margin >= 0.03: return 11, f"{margin:.0%} margin"
    if margin >= 0:    return  6, f"{margin:.0%} margin (thin)"
    return 2, f"{margin:.0%} margin (loss)"


def compute_score(data, revenue, net_result):
    oc      = data.get("oc", {})
    gl      = data.get("gleif", {})
    san     = data.get("sanctions", {})
    news    = data.get("news", {})
    fin     = data.get("financials", {})
    profile = data.get("ch_profile", {})
    signals = {}
    hard_flags = []

    if san.get("flagged"):
        return {"score": 0, "verdict": "do_not_proceed",
                "signals": {"Sanctions": ("FLAGGED 🚨", 0, 0)},
                "hard_flags": ["Sanctioned entity"], "summary_data": {}}

    if profile.get("found") and (profile.get("is_liquidation") or profile.get("is_administration")):
        status = "liquidation" if profile.get("is_liquidation") else "administration"
        return {"score": 0, "verdict": "do_not_proceed",
                "signals": {"Legal Status": (f"IN {status.upper()} 🚨", 0, 15)},
                "hard_flags": [f"Company is in {status}"], "summary_data": {}}

    inactive = oc.get("inactive", False) or (
        (oc.get("status") or "").lower() in ["dissolved", "inactive", "closed", "struck off"])
    if inactive or (profile.get("found") and profile.get("is_dissolved")):
        return {"score": 5, "verdict": "do_not_proceed",
                "signals": {"Legal Status": ("Inactive / Dissolved", 0, 15)},
                "hard_flags": ["Company is inactive or dissolved"], "summary_data": {}}

    auto_rev = fin.get("revenue") if fin.get("found") and fin.get("revenue") else None
    auto_net = fin.get("net_income") if fin.get("found") else None
    final_rev = auto_rev or revenue or 0
    final_net = auto_net if auto_net is not None else (net_result or 0)
    rev_source = f"Auto · {fin['source']}" if auto_rev else ("Manual entry" if revenue else None)

    r_pts, r_label = revenue_pts(final_rev)
    signals["Annual Revenue"] = (
        f"{r_label}" + (f"  ·  {rev_source}" if rev_source else ""), r_pts, 40
    )

    n_pts, n_label = net_result_pts(final_net, final_rev)
    signals["Net Result"] = (n_label, n_pts, 20)

    s_pts = 15 if oc.get("found") else 7
    signals["Legal Status"] = ("Active" if oc.get("found") else "Unknown", s_pts, 15)

    age = company_age_years(oc.get("incorporation_date", ""))
    a_pts = (10 if age >= 10 else 7 if age >= 5 else 4 if age >= 2 else 1) if age else 5
    signals["Company Age"] = (f"{age:.1f} years" if age else "Unknown", a_pts, 10)

    cc = (oc.get("jurisdiction") or gl.get("country") or "")[:2].upper()
    if cc:
        tier  = country_risk_tier(cc)
        c_pts = {1: 10, 2: 6, 3: 2}[tier]
        c_lbl = f"{COUNTRY_NAMES.get(cc, cc)} — {['','Low','Medium','High'][tier]} risk"
    else:
        tier, c_pts, c_lbl = 2, 5, "Unknown"
    signals["Country Risk"] = (c_lbl, c_pts, 10)

    if gl.get("found"):
        l_pts = 5 if gl.get("lei_status") == "ISSUED" else 2
        l_lbl = "Verified" if gl.get("lei_status") == "ISSUED" else gl.get("lei_status", "—")
    else:
        l_pts, l_lbl = 2, "Not found"
    signals["LEI"] = (l_lbl, l_pts, 5)

    total = r_pts + n_pts + s_pts + a_pts + c_pts + l_pts

    soft_flags = []
    trend_label = fin.get("trend_label")
    yoy_pct = fin.get("yoy_pct")
    if trend_label == "declining" and yoy_pct is not None:
        soft_flags.append(f"Revenue declining {yoy_pct:+.0%} year-over-year")
        total -= 5
    elif trend_label == "growing" and yoy_pct is not None:
        soft_flags.append(f"Revenue growing {yoy_pct:+.0%} year-over-year")

    if profile.get("accounts_overdue"):
        soft_flags.append("Accounts overdue at Companies House")
        total -= 8
    if profile.get("confirmation_overdue"):
        soft_flags.append("Confirmation statement overdue")
        total -= 3
    if profile.get("has_insolvency_history"):
        soft_flags.append("Prior insolvency history")
        total -= 5

    if news.get("found") and news.get("articles"):
        neg_count = sum(1 for a in news["articles"] if a.get("sentiment") == "negative")
        if neg_count >= 2:
            soft_flags.append(f"{neg_count} credit-negative news articles")
            total -= 5
        elif neg_count == 1:
            soft_flags.append("1 credit-negative news article")
            total -= 2

    normalised = max(0, min(100, round(total)))

    if not final_rev:       verdict = "caution"
    elif normalised >= 70:  verdict = "proceed"
    elif normalised >= 40:  verdict = "caution"
    else:                   verdict = "do_not_proceed"

    return {
        "score": normalised, "verdict": verdict, "signals": signals,
        "hard_flags": hard_flags, "soft_flags": soft_flags,
        "summary_data": {
            "name": oc.get("name") or gl.get("legal_name") or "",
            "status": oc.get("status") or "",
            "company_type": oc.get("company_type") or "",
            "country": COUNTRY_NAMES.get(cc, cc) if cc else "unknown location",
            "country_tier": tier if cc else 2,
            "age": age, "inc_date": oc.get("incorporation_date", ""),
            "lei": gl.get("lei") if gl.get("found") else None,
            "lei_status": gl.get("lei_status") if gl.get("found") else None,
            "address": oc.get("registered_address") or gl.get("city") or "",
            "source_url": oc.get("source_url") or "",
            "revenue": final_rev, "net_result": final_net, "rev_source": rev_source,
            "trend_label": trend_label, "yoy_pct": yoy_pct,
        },
    }


def build_summary(name, result, data):
    s, v, score = result["summary_data"], result["verdict"], result["score"]
    news = data.get("news", {})
    parts = []
    display = s.get("name") or name

    identity = []
    if s.get("company_type"): identity.append(s["company_type"])
    if s.get("country"):      identity.append(f"registered in {s['country']}")
    if s.get("age") and s.get("inc_date"):
        identity.append(f"incorporated in {s['inc_date'][:4]} ({int(s['age'])} years ago)")
    if s.get("address"):      identity.append(f"with address at {s['address']}")
    parts.append(f"**{display}** is a {', '.join(identity)}." if identity
                 else f"**{display}** was found in public registration records.")

    rev, net = s.get("revenue"), s.get("net_result")
    if rev:
        rev_str = f"€{rev/1e9:.1f}B" if rev >= 1e9 else f"€{rev/1e6:.0f}M" if rev >= 1e6 else f"€{rev:,.0f}"
        src = s.get("rev_source") or ""
        parts.append(f"Annual revenue is **{rev_str}**{f' ({src})' if src else ''}.")
        if net and rev:
            margin = net / rev
            parts.append(f"Net margin is {margin:.0%} ({'healthy' if margin >= 0.08 else 'thin' if margin >= 0 else 'negative'}).")
        trend = s.get("trend_label")
        yoy   = s.get("yoy_pct")
        if trend == "declining" and yoy is not None:
            parts.append(f"Revenue is **declining** ({yoy:+.0%} year-over-year).")
        elif trend == "growing" and yoy is not None:
            parts.append(f"Revenue is growing ({yoy:+.0%} year-over-year).")
        elif trend == "flat" and yoy is not None:
            parts.append(f"Revenue is roughly flat ({yoy:+.0%} year-over-year).")
    else:
        parts.append("Revenue was not entered — score reflects registration data only.")

    tier = s.get("country_tier", 2)
    country = s.get("country", "")
    if tier == 1:   parts.append(f"{country} is a **low-risk jurisdiction**.")
    elif tier == 2: parts.append(f"{country} carries **moderate jurisdiction risk**.")
    elif tier == 3: parts.append(f"{country} is a **high-risk jurisdiction** — enhanced due diligence recommended.")

    if news.get("found") and news.get("articles"):
        neg = sum(1 for a in news["articles"] if a.get("sentiment") == "negative")
        if neg == 0:   parts.append("No credit-negative media coverage detected.")
        elif neg == 1: parts.append("One credit-negative news article found — review before proceeding.")
        else:          parts.append(f"{neg} credit-negative articles found — warrants investigation.")

    if v == "proceed":
        parts.append(f"**Overall, this looks like a reasonable prospect** (score: {score}/100).")
    elif v == "caution":
        parts.append(f"**{'Enter revenue to complete the assessment' if not rev else 'Proceed with caution'}** (score: {score}/100).")
    else:
        parts.append(f"**Do not proceed** without senior approval (score: {score}/100).")
    return " ".join(parts)


def build_assessment_json(name, selected, result, data):
    return {
        "assessment": {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "query_name": name,
            "selected_company": selected,
            "verdict": result["verdict"],
            "score": result["score"],
            "hard_flags": result.get("hard_flags", []),
            "soft_flags": result.get("soft_flags", []),
            "signals": {k: {"label": lbl, "points": pts, "max_points": mx}
                        for k, (lbl, pts, mx) in result["signals"].items()},
        },
        "sources": {
            "opencorporates": data.get("oc", {}),
            "gleif":          data.get("gleif", {}),
            "sanctions":      data.get("sanctions", {}),
            "financials":     data.get("financials", {}),
            "ch_profile":     data.get("ch_profile", {}),
            "news":           data.get("news", {}),
        },
    }


# ─────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────

JURISDICTION_MAP = {
    "Any country": "", "Netherlands": "nl", "United Kingdom": "gb",
    "Germany": "de", "Belgium": "be", "France": "fr", "Spain": "es",
    "Italy": "it", "United States": "us", "China": "cn", "Singapore": "sg",
    "India": "in", "Brazil": "br", "Turkey": "tr", "UAE": "ae",
    "Dominican Republic": "do", "South Africa": "za", "Australia": "au",
    "Poland": "pl", "Sweden": "se", "Denmark": "dk", "Norway": "no",
}


def _current_step():
    """Return 1-5 based on how far through the workflow we are."""
    if st.session_state.get("financials_applied") or st.session_state.get("analysis_data"):
        data_tuple = st.session_state.get("analysis_data")
        if data_tuple and st.session_state.get("financials_applied"):
            return 5
        if data_tuple:
            return 4
    if st.session_state.get("selected_co"):
        return 3
    if st.session_state.get("candidates") is not None:
        return 2
    return 1


def _signal_color(pts, max_pts):
    if max_pts == 0:
        return "red"
    pct = pts / max_pts
    return "green" if pct >= 0.75 else "yellow" if pct >= 0.45 else "red"


def _render_signal_cards(signals):
    """Render the 6 signal cards as a custom HTML grid."""
    cards_html = '<div class="signal-grid">'
    for factor, (label, pts, max_pts) in signals.items():
        color = _signal_color(pts, max_pts)
        icon = "●" if color == "green" else "◐" if color == "yellow" else "○"
        cards_html += f"""
        <div class="signal-card {color}">
            <div class="signal-factor">{factor}</div>
            <div class="signal-label">{label}</div>
            <div class="signal-pts">{pts} / {max_pts} pts</div>
        </div>"""
    cards_html += "</div>"
    st.markdown(cards_html, unsafe_allow_html=True)


def _render_verdict_banner(verdict, display_name, score, fin_year):
    cls = {"proceed": "verdict-proceed", "caution": "verdict-caution"}.get(verdict, "verdict-stop")
    icon = {"proceed": "✓", "caution": "⚠", "do_not_proceed": "✕"}.get(verdict, "")
    title = {
        "proceed": f"{icon}  Proceed — {display_name}",
        "caution": f"{icon}  Proceed with Caution — {display_name}",
        "do_not_proceed": f"{icon}  Do Not Proceed — {display_name}",
    }[verdict]

    col_left, col_right = st.columns([5, 1])
    with col_left:
        st.markdown(f"""
        <div class="{cls}">
            <div class="verdict-title">{title}</div>
            <div class="verdict-meta">FY{fin_year} · {date.today().isoformat()} · Internal use only</div>
        </div>""", unsafe_allow_html=True)
    with col_right:
        score_color = "#2a6b3c" if verdict == "proceed" else "#c48f00" if verdict == "caution" else "#8a1818"
        st.markdown(f"""
        <div style="text-align:center; padding: 12px 0;">
            <div style="font-family:Georgia,serif; font-size:52px; font-weight:700; color:{score_color}; line-height:1">{score}</div>
            <div style="font-size:12px; color:#9aaab8; margin-top:2px">out of 100</div>
        </div>""", unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────────
# SIDEBAR
# ─────────────────────────────────────────────────────────────

with st.sidebar:
    # Header / branding
    if LOGO_SRC:
        st.markdown(f"""
        <div class="sidebar-header">
            <img src="{LOGO_SRC}" alt="IFC logo">
            <div class="app-title">Prospect Lookup</div>
        </div>""", unsafe_allow_html=True)
    else:
        st.markdown("""
        <div class="sidebar-header">
            <div style="font-family:Georgia,serif; font-size:22px; font-weight:700; color:#0d2240; margin-bottom:4px">IFC</div>
            <div class="app-title">Prospect Lookup</div>
        </div>""", unsafe_allow_html=True)

    # Step tracker
    step = _current_step()
    steps = ["Search company", "Select match", "Fetch data", "Enter financials", "Assessment"]
    items_html = ""
    for i, label in enumerate(steps, 1):
        cls = "done" if i < step else "active" if i == step else ""
        num_content = "✓" if i < step else str(i)
        items_html += f'<li class="{cls}"><span class="step-num">{num_content}</span>{label}</li>'
    st.markdown(f'<ul class="step-list">{items_html}</ul>', unsafe_allow_html=True)

    st.markdown('<div class="sidebar-section">Search</div>', unsafe_allow_html=True)

    company_name = st.text_input("Company name", placeholder="e.g. Brenntag UK")
    country = st.selectbox("Jurisdiction", list(JURISDICTION_MAP.keys()))
    search = st.button("Search", type="primary", use_container_width=True)

    if st.session_state["lookup_count"] > 0:
        remaining = MAX_LOOKUPS - st.session_state["lookup_count"]
        st.markdown(
            f'<div style="font-size:11px; color:#4a7a9b; text-align:center; margin-top:8px">'
            f'{remaining} lookups remaining this session</div>',
            unsafe_allow_html=True
        )

    # Reset button if there's an active search
    if st.session_state.get("candidates") is not None:
        st.markdown('<div style="margin-top: 16px"></div>', unsafe_allow_html=True)
        if st.button("↩ New search", use_container_width=True):
            for k in ["candidates", "selected_co", "analysis_data",
                      "revenue", "net_result", "financials_applied"]:
                st.session_state[k] = None if k in ["candidates", "selected_co", "analysis_data"] else 0
            st.session_state["financials_applied"] = False
            st.rerun()


# ─────────────────────────────────────────────────────────────
# MAIN AREA HEADER
# ─────────────────────────────────────────────────────────────

st.markdown("""
<div class="main-header">
    <div>
        <div class="main-header h1" style="font-family:Georgia,serif; font-size:22px; font-weight:700; color:#0d2240;">
            Credit Risk Screening
        </div>
        <div class="subtitle">Search a company, enter their revenue, get an instant risk assessment.</div>
    </div>
</div>""", unsafe_allow_html=True)

# ─────────────────────────────────────────────────────────────
# IDLE STATE
# ─────────────────────────────────────────────────────────────

if not company_name.strip() and st.session_state.get("candidates") is None:
    st.markdown("""
    <div class="ifc-card" style="border-left: 3px solid #0d2240; max-width: 600px;">
        <div class="card-label">How it works</div>
        <div style="font-size:14px; color:#4a5568; line-height:1.7; margin-top:8px">
            <b>1. Search</b> — enter a company name in the sidebar and select a jurisdiction.<br>
            <b>2. Select</b> — pick the correct match from the results.<br>
            <b>3. Review</b> — automatic data is fetched from Companies House, GLEIF, sanctions lists, and news.<br>
            <b>4. Enter financials</b> — pre-filled where available, or enter manually from the filing.<br>
            <b>5. Assess</b> — get a scored risk profile with a proceed / caution / do not proceed verdict.
        </div>
    </div>""", unsafe_allow_html=True)
    st.stop()

# ─────────────────────────────────────────────────────────────
# STEP 1: SEARCH
# ─────────────────────────────────────────────────────────────

if search:
    if st.session_state["lookup_count"] >= MAX_LOOKUPS:
        st.error("Session lookup limit reached (20). Start a new browser session.")
        st.stop()
    with st.spinner(f"Searching for **{company_name}**…"):
        candidates = fetch_candidates(company_name.strip(), JURISDICTION_MAP[country])
    st.session_state.update({
        "candidates": candidates,
        "selected_co": None,
        "analysis_data": None,
        "revenue": 0,
        "net_result": 0,
        "fin_year": date.today().year - 1,
        "financials_applied": False,
    })

candidates = st.session_state.get("candidates")
if candidates is None:
    st.stop()
if not candidates:
    st.warning(
        f"No companies found matching **{company_name}**. "
        "Try a shorter name or select a specific country."
    )
    st.stop()

# ─────────────────────────────────────────────────────────────
# STEP 2: COMPANY PICKER
# ─────────────────────────────────────────────────────────────

def candidate_label(c):
    parts = [p for p in [
        c.get("jurisdiction", ""),
        c.get("company_number", ""),
        c.get("company_type", ""),
        f"est. {c['incorporation_date'][:4]}" if c.get("incorporation_date") else "",
        c.get("status", ""),
    ] if p]
    return f"{c['name']}  —  {' · '.join(parts)}"

labels = [candidate_label(c) for c in candidates]

if len(candidates) > 1 and not st.session_state.get("selected_co"):
    st.markdown('<div class="ifc-card">', unsafe_allow_html=True)
    st.markdown("**Multiple matches — select the correct company:**")
    chosen = st.radio("Select", labels, label_visibility="collapsed", key="picker")
    if st.button("Confirm selection →", type="primary"):
        st.session_state.update({
            "selected_co": candidates[labels.index(chosen)],
            "analysis_data": None, "revenue": 0,
            "net_result": 0, "financials_applied": False,
        })
    st.markdown("</div>", unsafe_allow_html=True)
    st.stop()
elif not st.session_state.get("selected_co"):
    st.session_state["selected_co"] = candidates[0]

selected = st.session_state["selected_co"]

# ─────────────────────────────────────────────────────────────
# STEP 3: BACKGROUND ANALYSIS
# ─────────────────────────────────────────────────────────────

if st.session_state.get("analysis_data") is None:
    with st.spinner(f"Fetching data for **{selected['name']}**…"):
        t0 = time.time()
        jurisdiction   = selected.get("jurisdiction", "")
        company_number = selected.get("company_number", "")

        tasks = {
            "gleif":      (fetch_gleif,      (selected["name"],)),
            "sanctions":  (fetch_sanctions,  (selected["name"],)),
            "financials": (fetch_financials, (selected["name"], jurisdiction, company_number)),
        }
        if jurisdiction.upper() == "GB" and company_number:
            tasks["ch_profile"] = (fetch_ch_company_profile, (company_number,))

        partial = {"oc": {**selected, "found": True}}
        with ThreadPoolExecutor(max_workers=4) as ex:
            futures = {ex.submit(fn, *args): key for key, (fn, args) in tasks.items()}
            for future in as_completed(futures):
                partial[futures[future]] = future.result()
        partial["news"] = fetch_news(selected["name"])
        elapsed = time.time() - t0
    st.session_state["analysis_data"] = (partial, elapsed)
    st.session_state["lookup_count"] += 1

data, elapsed = st.session_state["analysis_data"]
fin        = data.get("financials", {})
ch_profile = data.get("ch_profile", {})
auto_revenue = fin.get("revenue") if fin.get("found") and fin.get("revenue") else None

# ─────────────────────────────────────────────────────────────
# STEP 4: COMPANY HEADER + FINANCIALS
# ─────────────────────────────────────────────────────────────

cc = (selected.get("jurisdiction") or "")[:2].upper()
source_name, source_url = FINANCIAL_SOURCE_URLS.get(cc, ("public registry / annual report", ""))

# Company header card
st.markdown(f"""
<div class="ifc-card" style="border-left: 3px solid #0d2240;">
    <div style="display:flex; justify-content:space-between; align-items:flex-start;">
        <div>
            <div style="font-family:Georgia,serif; font-size:18px; font-weight:700; color:#0d2240; margin-bottom:4px">
                {selected['name']}
            </div>
            <div style="font-size:12px; color:#9aaab8;">
                {selected.get('jurisdiction','—')} &nbsp;·&nbsp;
                Reg: {selected.get('company_number','—')} &nbsp;·&nbsp;
                Status: {selected.get('status','—')} &nbsp;·&nbsp;
                Inc: {selected.get('incorporation_date','—')}
            </div>
        </div>
    </div>
</div>""", unsafe_allow_html=True)

# CH health flags (UK only)
if ch_profile.get("found"):
    flags = []
    if ch_profile.get("is_liquidation"):        flags.append("🚨 In liquidation")
    if ch_profile.get("is_administration"):     flags.append("🚨 In administration")
    if ch_profile.get("is_dissolved"):          flags.append("🚨 Dissolved")
    if ch_profile.get("accounts_overdue"):      flags.append("⚠️ Accounts overdue")
    if ch_profile.get("confirmation_overdue"):  flags.append("⚠️ Confirmation statement overdue")
    if ch_profile.get("has_insolvency_history"): flags.append("⚠️ Prior insolvency history")
    if flags:
        st.error(" · ".join(flags))
    else:
        due = ch_profile.get("next_accounts_due") or "—"
        st.success(f"✅ Active · next accounts due {due}")

# Financial data card
st.markdown('<div class="ifc-card">', unsafe_allow_html=True)
st.markdown('<div class="card-label">Financial Data</div>', unsafe_allow_html=True)

auto_net = fin.get("net_income") if fin.get("found") else None
if auto_revenue:
    rev_str = f"£{fin.get('revenue_gbp',0)/1e6:.1f}M" if fin.get("revenue_gbp") else f"€{auto_revenue/1e6:.1f}M"
    st.success(
        f"✅ **Revenue fetched automatically from {fin.get('source','')}**: {rev_str} "
        f"(≈ €{auto_revenue/1e6:.1f}M)  ·  FY{fin.get('fiscal_year','')}"
    )
    if fin.get("currency_note"):
        st.caption(f"ℹ️ {fin['currency_note']}")
    if fin.get("filing_url"):
        st.caption(f"[📄 View original filing on Companies House]({fin['filing_url']})")

    if fin.get("pdf_extracted"):
        with st.expander("🔍 Show extracted source lines (verify against PDF)"):
            if fin.get("pdf_source_line_revenue"):
                st.markdown(f"**Revenue line:** `{fin['pdf_source_line_revenue']}`")
            if fin.get("pdf_source_line_profit"):
                st.markdown(f"**Profit line:** `{fin['pdf_source_line_profit']}`")
            if fin.get("raw_text"):
                st.caption("Raw extracted text (first 2000 chars):")
                st.code(fin["raw_text"][:2000], language="text")

    trend = fin.get("trend") or []
    if len(trend) >= 2:
        st.markdown("**Revenue trend**")
        trend_cols = st.columns(len(trend))
        for i, t in enumerate(trend):
            with trend_cols[i]:
                rev_gbp = t.get("revenue_gbp")
                yr      = t.get("fiscal_year", "—")
                if rev_gbp:
                    st.metric(f"FY{yr}", f"£{rev_gbp/1e6:.1f}M")
                else:
                    st.metric(f"FY{yr}", "—")
        yoy = fin.get("yoy_pct")
        tlbl = fin.get("trend_label", "")
        if yoy is not None:
            arrow = "📈" if tlbl == "growing" else "📉" if tlbl == "declining" else "➡️"
            st.caption(f"{arrow} Year-over-year: **{yoy:+.1%}** ({tlbl})")
else:
    reason = fin.get("reason")
    if reason:
        st.warning(f"⚠️ {reason}")
    else:
        st.info("No automatic extraction available for this jurisdiction.")

    if fin.get("net_income_gbp") is not None and not fin.get("revenue"):
        ni = fin["net_income_gbp"]
        st.caption(f"ℹ️ Net profit was disclosed: £{ni/1e3:,.0f}K  ·  FY{fin.get('fiscal_year','—')}")

    if fin.get("filing_url"):
        st.markdown(f"📄 **[View filing history on Companies House]({fin['filing_url']})**")
    elif cc == "GB" and selected.get("company_number"):
        ch_url = f"https://find-and-update.company-information.service.gov.uk/company/{selected['company_number']}/filing-history"
        st.markdown(f"📄 **[View filing history on Companies House]({ch_url})**")

    has_legal_omission = fin.get("is_micro_entity") or fin.get("is_filleted")
    if cc == "GB" and not has_legal_omission:
        with st.expander("📎 Or upload the PDF and we'll try to extract automatically"):
            st.caption(
                "Downloaded the PDF from Companies House? Drop it below, we'll try to pull "
                "turnover and profit and pre-fill the fields."
            )
            uploaded = st.file_uploader(
                "PDF file", type=["pdf"], label_visibility="collapsed",
                key=f"pdf_upload_{selected.get('company_number','x')}",
            )
            if uploaded is not None:
                pdf_bytes = uploaded.getvalue()
                ocr_key = f"ocr_requested_{selected.get('company_number','x')}_{len(pdf_bytes)}"
                if ocr_key not in st.session_state:
                    st.session_state[ocr_key] = False
                use_ocr = st.session_state[ocr_key]

                with st.spinner("Running OCR on scanned PDF (this can take 30-90 seconds)…" if use_ocr else "Reading PDF…"):
                    pdf_result = extract_uploaded_pdf(pdf_bytes, use_ocr=use_ocr)

                if pdf_result.get("found"):
                    new_data = {**data, "financials": {**fin, **pdf_result}}
                    st.session_state["analysis_data"] = (new_data, elapsed)
                    src_label = pdf_result.get("source", "uploaded PDF")
                    st.success(
                        f"✅ Extracted from {src_label}: revenue £"
                        f"{(pdf_result.get('revenue_gbp') or 0)/1e6:.2f}M"
                        + (f", profit £{(pdf_result.get('net_income_gbp') or 0)/1e3:.0f}K"
                           if pdf_result.get("net_income_gbp") else "")
                    )
                    if pdf_result.get("ocr_extracted"):
                        for warn in (pdf_result.get("ocr_warnings") or []):
                            st.warning(f"⚠ {warn}")
                        if pdf_result.get("pdf_source_line_revenue"):
                            st.caption(f"Source line for revenue: `{pdf_result['pdf_source_line_revenue']}`")
                        if pdf_result.get("pdf_source_line_profit"):
                            st.caption(f"Source line for profit:  `{pdf_result['pdf_source_line_profit']}`")
                    st.rerun()
                else:
                    if pdf_result.get("is_scan_only") and not use_ocr and pdf_result.get("ocr_available"):
                        st.info(
                            "📷 This PDF appears to be a scanned image. We can try OCR, "
                            "but it takes 30-90 seconds and figures will need verification."
                        )
                        if st.button("🔍 Try OCR on this PDF",
                                     key=f"ocr_btn_{selected.get('company_number','x')}"):
                            st.session_state[ocr_key] = True
                            st.rerun()
                    elif pdf_result.get("ocr_attempted"):
                        st.warning(f"OCR completed but couldn't find figures: {pdf_result.get('reason','')}")
                        if pdf_result.get("raw_text"):
                            with st.expander("Show OCR text"):
                                st.code(pdf_result["raw_text"][:5000], language="text")
                    else:
                        st.warning(f"Couldn't extract figures automatically: {pdf_result.get('reason','')}")
                        if pdf_result.get("raw_text"):
                            with st.expander("Show raw text from your PDF"):
                                st.code(pdf_result["raw_text"][:5000], language="text")

    if cc != "GB":
        manual_source = fin.get("manual_source") or source_name
        manual_url    = fin.get("manual_url") or source_url
        if manual_url:
            st.info(f"Enter the latest annual figures below. Find them at: **[{manual_source}]({manual_url})**")
        else:
            st.info("Enter the company's latest annual revenue and net result from their annual filing.")
    else:
        st.caption("↓ Enter the figures from the filing manually below, then click Run Assessment.")

# Financials inputs (always shown)
st.markdown('<div style="margin-top:12px"></div>', unsafe_allow_html=True)
fc1, fc2, fc3 = st.columns([2, 2, 1])
with fc1:
    rev_input = st.number_input(
        "Annual Revenue (€)", min_value=0, step=100_000,
        value=int(auto_revenue) if auto_revenue else st.session_state["revenue"],
        help="Total annual turnover in euros. Pre-filled if fetched automatically.",
    )
with fc2:
    net_input = st.number_input(
        "Net Result (€)", min_value=-100_000_000, step=50_000,
        value=int(auto_net) if auto_net is not None else st.session_state["net_result"],
        help="Net profit or loss after tax. Enter negative for a loss.",
    )
with fc3:
    fy_default = int(fin.get("fiscal_year", date.today().year - 1)) if fin.get("fiscal_year") else st.session_state["fin_year"]
    year_input = st.number_input(
        "FY", min_value=2015, max_value=date.today().year,
        value=fy_default,
    )

if st.button("Run Assessment →", type="primary", use_container_width=True):
    st.session_state.update({
        "revenue": int(rev_input),
        "net_result": int(net_input),
        "fin_year": int(year_input),
        "financials_applied": True,
    })
    if isinstance(data, dict) and isinstance(data.get("financials"), dict):
        data["financials"]["fiscal_year"] = str(int(year_input))
        st.session_state["analysis_data"] = (data, elapsed)

st.markdown("</div>", unsafe_allow_html=True)  # close ifc-card

if not st.session_state.get("financials_applied") and not auto_revenue:
    st.caption("↑ Enter financials and click **Run Assessment** to see the full score.")
    st.stop()

# ─────────────────────────────────────────────────────────────
# STEP 5: RESULTS
# ─────────────────────────────────────────────────────────────

revenue    = st.session_state["revenue"] or (int(auto_revenue) if auto_revenue else 0)
net_result = st.session_state["net_result"]

result       = compute_score(data, revenue, net_result)
verdict      = result["verdict"]
score        = result["score"]
signals      = result["signals"]
sd           = result["summary_data"]
news         = data.get("news", {})
display_name = sd.get("name") or selected["name"]

st.markdown('<div style="height:8px"></div>', unsafe_allow_html=True)

# Verdict banner + score
_render_verdict_banner(verdict, display_name, score, st.session_state["fin_year"])

# Hard / soft flags
for hf in result.get("hard_flags", []):
    st.error(f"🚨 {hf}")
for sf in result.get("soft_flags", []):
    st.warning(f"⚠️ {sf}")

# Narrative summary
narrative = build_summary(selected["name"], result, data)
st.markdown(f"""
<div class="ifc-card">
    <div class="card-label">Assessment Summary</div>
    <div style="font-size:14px; line-height:1.7; margin-top:8px; color:#2d3748">{narrative}</div>
</div>""", unsafe_allow_html=True)

# Signal breakdown
st.markdown('<div class="ifc-card">', unsafe_allow_html=True)
st.markdown('<div class="card-label">Signal Breakdown</div>', unsafe_allow_html=True)
st.markdown('<div style="margin-top:10px"></div>', unsafe_allow_html=True)
_render_signal_cards(signals)
st.markdown("</div>", unsafe_allow_html=True)

# Company details + news (two columns)
left_col, right_col = st.columns([3, 2])

with left_col:
    details = {
        "Legal Name":   display_name,
        "Reg. Number":  selected.get("company_number") or "—",
        "Type":         selected.get("company_type") or "—",
        "Status":       selected.get("status") or "—",
        "Incorporated": selected.get("incorporation_date") or "—",
        "Address":      selected.get("registered_address") or "—",
    }
    lei = sd.get("lei")
    if lei:
        details["LEI"] = f"{lei} ({sd.get('lei_status','—')})"

    rows_html = ""
    for k, v in details.items():
        rows_html += f'<div class="detail-row"><span class="detail-key">{k}</span><span class="detail-val">{v}</span></div>'
    if selected.get("source_url"):
        rows_html += f'<div class="detail-row"><span class="detail-key">OpenCorporates</span><span class="detail-val"><a href="{selected["source_url"]}" target="_blank">View record ↗</a></span></div>'

    st.markdown(f"""
    <div class="ifc-card">
        <div class="card-label">Company Details</div>
        <div style="margin-top:10px">{rows_html}</div>
    </div>""", unsafe_allow_html=True)

with right_col:
    news_html = ""
    if news.get("found") and news.get("articles"):
        for a in news["articles"]:
            snt   = {"negative": "🔴", "neutral": "⚪"}.get(a.get("sentiment"), "⚪")
            title = a.get("title", "")
            url   = a.get("url", "")
            pub   = a.get("published", "")
            link  = f'<a href="{url}" target="_blank">{title}</a>' if url else title
            news_html += f'<div class="news-item">{snt} {link}<br><span style="font-size:11px;color:#9aaab8">{pub}</span></div>'
    else:
        news_html = '<div style="font-size:13px;color:#9aaab8">No recent news found.</div>'

    st.markdown(f"""
    <div class="ifc-card">
        <div class="card-label">Recent News</div>
        <div style="margin-top:10px">{news_html}</div>
    </div>""", unsafe_allow_html=True)

# Export
st.markdown('<div class="ifc-card">', unsafe_allow_html=True)
st.markdown('<div class="card-label">Export Assessment</div>', unsafe_allow_html=True)
st.caption("Download the assessment to share with colleagues or attach to a credit file.")

assessment_json = json.dumps(
    build_assessment_json(selected["name"], selected, result, data),
    indent=2, default=str,
)
pdf_bytes = build_pdf(selected["name"], selected, result, data, narrative)

base_name = (selected.get("company_number") or display_name).replace(" ", "_")
today_iso = date.today().isoformat()

dl1, dl2 = st.columns(2)
with dl1:
    st.download_button(
        "📄 Download PDF report",
        data=pdf_bytes,
        file_name=f"credit_assessment_{base_name}_{today_iso}.pdf",
        mime="application/pdf",
        use_container_width=True,
        help="Branded credit memo for internal circulation or credit file attachment.",
    )
with dl2:
    st.download_button(
        "🗂️ Download raw data (JSON)",
        data=assessment_json,
        file_name=f"assessment_{base_name}_{today_iso}.json",
        mime="application/json",
        use_container_width=True,
        help="Every input, signal, and source used — for audit or reproducing the assessment.",
    )

st.markdown("</div>", unsafe_allow_html=True)

st.caption(
    "Sources: OpenCorporates · GLEIF · Financial Modeling Prep · Companies House · "
    "NewsData.io · EU Sanctions List · ECB FX rates  \n"
    "For internal use only — not a substitute for formal credit bureau reports."
)
