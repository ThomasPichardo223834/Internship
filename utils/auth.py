"""
utils/auth.py
Shared password gate — imported at the top of every page file.
"""

import base64
from pathlib import Path
import streamlit as st

_PASSWORD = st.secrets.get("DASHBOARD_PASSWORD", None)


def _logo_b64():
    for fname in ("ifc_logo.png", "ifc_logo.svg", "logo.png", "logo.svg"):
        p = Path(__file__).parent.parent / fname
        if p.exists():
            mime = "image/svg+xml" if fname.endswith(".svg") else "image/png"
            data = base64.b64encode(p.read_bytes()).decode()
            return f"data:{mime};base64,{data}"
    return None


def require_auth():
    if _PASSWORD is None:
        return
    if st.session_state.get("authenticated"):
        return

    logo_src = _logo_b64()

    st.markdown("""
    <style>
    [data-testid="stSidebar"]       { display: none !important; }
    [data-testid="stSidebarNav"]    { display: none !important; }
    header[data-testid="stHeader"]  { display: none !important; }

    .stApp { background: #0a1929 !important; }

    /* Narrow centered column */
    .block-container {
        max-width: 480px !important;
        padding-top: 12vh !important;
        padding-left: 24px !important;
        padding-right: 24px !important;
    }

    /* Kill webkit text override so our colors show */
    .block-container * {
        -webkit-text-fill-color: unset !important;
    }

    /* Input */
    .stTextInput input {
        background: #112236 !important;
        border: 1px solid #1e3a5f !important;
        border-radius: 8px !important;
        color: #e8f0f8 !important;
        font-size: 16px !important;
        padding: 14px 16px !important;
        height: auto !important;
    }
    .stTextInput input::placeholder { color: #4a7a9b !important; }
    .stTextInput input:focus {
        border-color: #2a6b3c !important;
        box-shadow: 0 0 0 3px rgba(42,107,60,0.2) !important;
    }
    .stTextInput label { display: none !important; }

    /* Sign in button */
    .stButton > button {
        width: 100% !important;
        background: #064169 !important;
        color: #ffffff !important;
        -webkit-text-fill-color: #ffffff !important;
        border: none !important;
        border-radius: 8px !important;
        font-size: 16px !important;
        font-weight: 600 !important;
        padding: 14px 0 !important;
        margin-top: 8px !important;
        letter-spacing: 0.03em !important;
        transition: background 0.15s !important;
    }
    .stButton > button:hover {
        background: #0d2240 !important;
        color: #ffffff !important;
        -webkit-text-fill-color: #ffffff !important;
    }

    .stAlert { border-radius: 8px !important; font-size: 14px !important; }
    </style>
    """, unsafe_allow_html=True)

    # Logo — big and centered
    if logo_src:
        st.markdown(
            f'<div style="text-align:center;margin-bottom:32px">'
            f'<img src="{logo_src}" style="width:220px"></div>',
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            '<div style="font-family:Georgia,serif;font-size:42px;font-weight:700;'
            'color:#064169;text-align:center;margin-bottom:32px">IFC</div>',
            unsafe_allow_html=True,
        )

    st.markdown(
        '<hr style="border:none;border-top:1px solid #1e3a5f;margin:0 0 28px 0">',
        unsafe_allow_html=True,
    )

    st.markdown(
        '<div style="font-family:Georgia,serif;font-size:26px;font-weight:700;'
        'color:#e8f0f8;text-align:center;margin-bottom:6px;'
        '-webkit-text-fill-color:#e8f0f8 !important">Prospect Lookup</div>',
        unsafe_allow_html=True,
    )
    st.markdown(
        '<div style="font-size:13px;color:#4a7a9b;text-align:center;'
        'letter-spacing:0.08em;text-transform:uppercase;margin-bottom:36px;'
        '-webkit-text-fill-color:#4a7a9b !important">'
        'Internal use only &nbsp;·&nbsp; Finance &amp; Control</div>',
        unsafe_allow_html=True,
    )

    pwd = st.text_input("Password", type="password",
                        placeholder="Enter access password",
                        label_visibility="collapsed")

    if st.button("Sign in", type="primary", use_container_width=True):
        if pwd == _PASSWORD:
            st.session_state["authenticated"] = True
            st.rerun()
        else:
            st.error("Incorrect password.")

    st.markdown(
        '<div style="font-size:12px;color:#2a4a6b;text-align:center;'
        'margin-top:32px;-webkit-text-fill-color:#2a4a6b !important">'
        'International Furan Chemicals &nbsp;·&nbsp; Credit Risk Dashboard</div>',
        unsafe_allow_html=True,
    )

    st.stop()
