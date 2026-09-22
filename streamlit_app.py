"""
CURT Inventory Assistant — Streamlit frontend.

Single-page app (per the brief) with:
- A phase toggle (Phase 1 rule-based / Phase 2 LLM-powered) — no restart needed
- A chat interface with per-phase, per-session message history
- A live inventory sidebar with search, category filter, and low-stock highlighting
- A tool-call inspector under each Phase 2 answer (the brief's "tool-calling execution flow")
- Response-time display for Phase 2 answers
- A session/memory info panel
- A system status panel (backend, database, configured model)

Assumes your project already has:
- app/service.py      -> InventoryService with get_all_parts()
- app/phase1.py        -> a function that answers a typed question (see PHASE1 section below —
                           adjust the function name/signature to match what you actually built)
- FastAPI running separately at API_URL, exposing POST /chat, GET /inventory, GET /health

Run with: streamlit run streamlit_app.py
(Run FastAPI separately: uvicorn app.main:app --reload)
"""

import os
import time
import uuid
from datetime import datetime

import pandas as pd
import requests
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

API_URL = os.getenv("API_URL", "http://localhost:8000")
LOW_STOCK_THRESHOLD = 5

# --------------------------------------------------------------------------
# Backend imports for Phase 1 + direct DB access (inventory sidebar works
# even if the FastAPI server isn't running, matching the brief: "the
# Streamlit app should still call your real Phase 1 logic against the
# real database.")
# --------------------------------------------------------------------------
# Ensure this service's own local SQLite copy exists and is seeded — each
# Railway service has an isolated filesystem, so this doesn't share a file
# with the FastAPI backend's copy. Both are seeded identically, so Phase 1
# and the inventory sidebar (which both read locally) still work correctly.
try:
    from app.db import init_db, seed_db
    init_db()
    seed_db()
except Exception:
    pass  # surfaced by the existing service/database status checks below
try:
    from app.service import InventoryService

    _service = InventoryService()
except Exception as exc:  # pragma: no cover - surfaced in the status panel instead
    _service = None
    _service_error = str(exc)

try:
    from app.phase1 import answer as phase1_answer
except Exception as exc:
    import traceback
    st.error(f"Phase 1 import failed: {exc}")
    st.code(traceback.format_exc())
    phase1_answer = None


# --------------------------------------------------------------------------
# Page config + design tokens
# --------------------------------------------------------------------------
st.set_page_config(
    page_title="CURT Inventory Assistant",
    page_icon="🏁",
    layout="wide",
    initial_sidebar_state="expanded",
)

NAVY = "#0B1120"
NAVY_SOFT = "#141C30"
ACCENT = "#5B4FE9"
ACCENT_SOFT = "#EDEBFC"
OK_GREEN = "#16A34A"
OK_GREEN_BG = "#DCFCE7"
LOW_RED = "#DC2626"
LOW_RED_BG = "#FEE2E2"
BG = "#F5F6FA"
CARD_BG = "#FFFFFF"
TEXT_MUTED = "#6B7280"

CSS = f"""
<style>
.stApp {{ background-color: {BG}; }}

section[data-testid="stSidebar"] {{
    background-color: {NAVY};
}}
section[data-testid="stSidebar"] * {{
    color: #E5E7EB !important;
}}

/* Fix: sidebar <code> (Session ID) was invisible — light gray text on the
   default light-gray Streamlit code background. Give it its own contrast. */
section[data-testid="stSidebar"] code {{
    background-color: #232C47 !important;
    color: #C7D2FE !important;
    padding: 2px 6px;
    border-radius: 4px;
}}

section[data-testid="stSidebar"] .stButton button {{
    width: 100%;
    border-radius: 8px;
    border: 1px solid #2A3450;
    background-color: {NAVY_SOFT};
    color: #E5E7EB;
}}
section[data-testid="stSidebar"] .stButton button:hover {{
    border-color: {ACCENT};
    color: #FFFFFF;
}}

.curt-card {{
    background-color: {NAVY_SOFT};
    border: 1px solid #232C47;
    border-radius: 10px;
    padding: 14px 16px;
    margin-bottom: 14px;
}}
.curt-card h4 {{
    margin: 0 0 10px 0;
    font-size: 0.85rem;
    letter-spacing: 0.02em;
}}
.curt-status-row {{
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 0.82rem;
    margin-bottom: 6px;
}}
.dot {{ height: 8px; width: 8px; border-radius: 50%; display: inline-block; }}
.dot-ok {{ background-color: #22C55E; }}
.dot-bad {{ background-color: #EF4444; }}

.main-header {{
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    margin-bottom: 4px;
}}
.main-header h1 {{ margin: 0; font-size: 1.7rem; }}
.main-header p {{ color: {TEXT_MUTED}; margin: 2px 0 18px 0; }}

.status-badge {{
    display: inline-block;
    padding: 2px 10px;
    border-radius: 999px;
    font-size: 0.75rem;
    font-weight: 600;
}}
.badge-ok {{ background-color: {OK_GREEN_BG}; color: {OK_GREEN}; }}
.badge-low {{ background-color: {LOW_RED_BG}; color: {LOW_RED}; }}

.inv-table {{ width: 100%; border-collapse: collapse; font-size: 0.85rem; }}
.inv-table th {{
    text-align: left;
    color: {TEXT_MUTED};
    font-weight: 600;
    padding: 6px 8px;
    border-bottom: 1px solid #E5E7EB;
}}
.inv-table td {{
    padding: 8px 8px;
    border-bottom: 1px solid #F0F1F5;
}}

.low-stock-box {{
    background-color: #FFFBEB;
    border: 1px solid #FDE68A;
    border-radius: 10px;
    padding: 12px 14px;
    margin-top: 14px;
}}
.low-stock-box h4 {{ margin: 0 0 8px 0; color: #92400E; font-size: 0.85rem; }}
.low-stock-item {{
    display: flex;
    justify-content: space-between;
    font-size: 0.85rem;
    padding: 3px 0;
}}

.session-box, .tips-box {{
    background-color: {CARD_BG};
    border: 1px solid #E5E7EB;
    border-radius: 10px;
    padding: 12px 14px;
    margin-top: 14px;
    font-size: 0.85rem;
}}
.session-box h4, .tips-box h4 {{ margin: 0 0 8px 0; font-size: 0.85rem; }}

.latency-tag {{
    color: {TEXT_MUTED};
    font-size: 0.75rem;
    margin-top: 4px;
}}
</style>
"""
st.markdown(CSS, unsafe_allow_html=True)


# --------------------------------------------------------------------------
# Session state
# --------------------------------------------------------------------------
def new_session():
    st.session_state.session_id = str(uuid.uuid4())
    st.session_state.messages = {"Phase 1": [], "Phase 2": []}
    st.session_state.last_item = None
    st.session_state.pending = False


if "session_id" not in st.session_state:
    new_session()
if "phase" not in st.session_state:
    st.session_state.phase = "Phase 2"
if "pending" not in st.session_state:
    st.session_state.pending = False


# --------------------------------------------------------------------------
# Sidebar
# --------------------------------------------------------------------------
with st.sidebar:
    st.markdown("### 🏁 CURT Inventory Assistant")
    st.caption("Cairo University Racing Team")
    st.markdown("---")

    # Current session
    st.markdown('<div class="curt-card"><h4>CURRENT SESSION</h4>', unsafe_allow_html=True)
    st.markdown(f"**Session ID**  \n`{st.session_state.session_id[:18]}…`")
    total_msgs = sum(len(v) for v in st.session_state.messages.values())
    st.markdown(f"**Messages:** {total_msgs}")
    st.markdown(f"**Last referenced item:** {st.session_state.last_item or '—'}")
    st.markdown("</div>", unsafe_allow_html=True)

    col_a, col_b = st.columns(2)
    with col_a:
        if st.button("🔄 New Session"):
            new_session()
            st.rerun()
    with col_b:
        if st.button("🗑️ Clear Chat"):
            st.session_state.messages[st.session_state.phase] = []
            st.rerun()

    # System status
    st.markdown('<div class="curt-card"><h4>SYSTEM STATUS</h4>', unsafe_allow_html=True)

    backend_ok = False
    try:
        r = requests.get(f"{API_URL}/health", timeout=1.5)
        backend_ok = r.status_code == 200
    except Exception:
        backend_ok = False
    dot = "dot-ok" if backend_ok else "dot-bad"
    label = "Connected" if backend_ok else "Offline"
    st.markdown(
        f'<div class="curt-status-row"><span class="dot {dot}"></span>'
        f"Backend (FastAPI) — {label}</div>",
        unsafe_allow_html=True,
    )

    db_ok = _service is not None
    dot = "dot-ok" if db_ok else "dot-bad"
    label = "Connected" if db_ok else "Unavailable"
    st.markdown(
        f'<div class="curt-status-row"><span class="dot {dot}"></span>'
        f"Database (SQLite) — {label}</div>",
        unsafe_allow_html=True,
    )

    model_name = os.getenv("GEMINI_MODEL", "not set")
    st.markdown(
        f'<div class="curt-status-row"><span class="dot dot-ok"></span>'
        f"LLM configured — {model_name}</div>",
        unsafe_allow_html=True,
    )
    st.markdown("</div>", unsafe_allow_html=True)

    # Architecture
    st.markdown(
        '<div class="curt-card"><h4>SYSTEM ARCHITECTURE</h4>'
        "Streamlit → FastAPI → Gemini → Tools → SQLite"
        "</div>",
        unsafe_allow_html=True,
    )

    st.caption("CURT Inventory Assistant v1.0 · Season 26-27")


# --------------------------------------------------------------------------
# Main header + phase toggle
# --------------------------------------------------------------------------
st.markdown(
    '<div class="main-header"><div>'
    "<h1>CURT Inventory Assistant</h1>"
    "<p>Ask anything about our parts inventory, quantities, locations and more.</p>"
    "</div></div>",
    unsafe_allow_html=True,
)

st.session_state.phase = st.radio(
    "Phase",
    ["Phase 1 (Rule-based)", "Phase 2 (LLM Powered)"],
    horizontal=True,
    label_visibility="collapsed",
    index=0 if st.session_state.phase == "Phase 1" else 1,
)
st.session_state.phase = "Phase 1" if "Phase 1" in st.session_state.phase else "Phase 2"
phase = st.session_state.phase

main_col, side_col = st.columns([2, 1], gap="large")


# --------------------------------------------------------------------------
# Chat column
# --------------------------------------------------------------------------
with main_col:
    for msg in st.session_state.messages[phase]:
        avatar = "🧑" if msg["role"] == "user" else "🤖"
        with st.chat_message(msg["role"], avatar=avatar):
            st.write(msg["content"])
            if msg.get("tool_calls"):
                with st.expander(f"🛠️ Tools Used ({len(msg['tool_calls'])})"):
                    for call in msg["tool_calls"]:
                        st.code(f"{call['name']}({call['args']})", language="python")
                        st.json(call["result"])
            if msg.get("latency") is not None:
                st.markdown(
                    f'<div class="latency-tag">⚡ Response time: {msg["latency"]:.2f}s</div>',
                    unsafe_allow_html=True,
                )

    # Fix: previously, submitting a question did the API call (up to ~30-60s) in the
    # SAME script run before Streamlit re-rendered, so the user's own message stayed
    # invisible until they typed again. Now we split it into two runs: the user's
    # bubble is appended and shown immediately (rerun #1), then the reply is fetched
    # on the very next run (rerun #2) while a spinner is visible.
    question = st.chat_input("Type your question here…")

    if question:
        st.session_state.messages[phase].append({"role": "user", "content": question})
        st.session_state.pending = True
        st.rerun()

    if st.session_state.get("pending"):
        with st.spinner("Thinking…"):
            last_q = st.session_state.messages[phase][-1]["content"]

            if phase == "Phase 1":
                if phase1_answer is None:
                    reply_text = (
                        "Phase 1 isn't wired up yet — update the `from app.phase1 import ...` "
                        "line at the top of this file to match your actual function."
                    )
                else:
                    try:
                        reply_text = phase1_answer(last_q)
                    except Exception as exc:
                        reply_text = f"Phase 1 error: {exc}"
                st.session_state.messages[phase].append({"role": "assistant", "content": reply_text})

            else:  # Phase 2
                start = time.time()
                try:
                    resp = requests.post(
                        f"{API_URL}/chat",
                        json={"session_id": st.session_state.session_id, "message": last_q},
                        timeout=60,
                    )
                    latency = time.time() - start
                    if resp.status_code == 200:
                        data = resp.json()
                        reply_text = data["response"]
                        tool_calls = data.get("tool_calls", [])
                        for call in tool_calls:
                            item = call.get("args", {}).get("item_name") or call.get("args", {}).get("category")
                            if item:
                                st.session_state.last_item = item
                    else:
                        reply_text = f"Backend error ({resp.status_code}): {resp.json().get('detail', 'unknown error')}"
                        tool_calls = []
                except requests.exceptions.ConnectionError:
                    latency = time.time() - start
                    reply_text = (
                        "⚠️ Can't reach the backend. Make sure FastAPI is running: "
                        "`uvicorn app.main:app --reload`"
                    )
                    tool_calls = []
                except Exception as exc:
                    latency = time.time() - start
                    reply_text = f"Unexpected error: {exc}"
                    tool_calls = []

                st.session_state.messages[phase].append(
                    {"role": "assistant", "content": reply_text, "tool_calls": tool_calls, "latency": latency}
                )

        st.session_state.pending = False
        st.rerun()


# --------------------------------------------------------------------------
# Inventory sidebar (right column)
# --------------------------------------------------------------------------
with side_col:
    st.markdown("#### 📦 Inventory")

    search = st.text_input("Search items…", label_visibility="collapsed", placeholder="Search items…")

    if _service is None:
        st.error(f"Database unavailable: {_service_error}")
        parts = []
    else:
        parts = _service.get_all_parts()

    df = pd.DataFrame(parts) if parts else pd.DataFrame(columns=["name", "quantity", "category", "location"])

    categories = ["All Categories"] + sorted(df["category"].unique().tolist()) if not df.empty else ["All Categories"]
    category = st.selectbox("Category", categories, label_visibility="collapsed")

    filtered = df.copy()
    if search:
        filtered = filtered[filtered["name"].str.contains(search, case=False, na=False)]
    if category != "All Categories":
        filtered = filtered[filtered["category"] == category]

    if filtered.empty:
        st.info("No items match your filters." if not df.empty else "No inventory data available.")
    else:
        rows_html = ""
        for _, row in filtered.iterrows():
            low = row["quantity"] < LOW_STOCK_THRESHOLD
            badge_class = "badge-low" if low else "badge-ok"
            badge_text = "Low Stock" if low else "OK"
            rows_html += (
                f"<tr><td>{row['name']}</td><td>{row['quantity']}</td>"
                f"<td>{row['category']}</td><td>{row['location']}</td>"
                f'<td><span class="status-badge {badge_class}">{badge_text}</span></td></tr>'
            )
        table_html = (
            '<table class="inv-table"><thead><tr>'
            "<th>Item</th><th>Qty</th><th>Category</th><th>Location</th><th>Status</th>"
            f"</tr></thead><tbody>{rows_html}</tbody></table>"
        )
        st.markdown(table_html, unsafe_allow_html=True)

    low_items = df[df["quantity"] < LOW_STOCK_THRESHOLD] if not df.empty else df
    if not low_items.empty:
        items_html = "".join(
            f'<div class="low-stock-item"><span>{r["name"]}</span><span>{r["quantity"]} left</span></div>'
            for _, r in low_items.iterrows()
        )
        st.markdown(
            f'<div class="low-stock-box"><h4>⚠️ Low Stock Items ({len(low_items)})</h4>{items_html}</div>',
            unsafe_allow_html=True,
        )

    st.markdown(
        '<div class="session-box"><h4>Session Info</h4>'
        f'Session ID: <code>{st.session_state.session_id[:12]}…</code><br>'
        f'Messages: {sum(len(v) for v in st.session_state.messages.values())}<br>'
        f'Last item: {st.session_state.last_item or "—"}'
        "</div>",
        unsafe_allow_html=True,
    )

    st.markdown(
        '<div class="tips-box"><h4>💡 Quick Tips</h4>'
        "• Use natural language — no keywords needed<br>"
        "• Ask about quantities, locations, or categories<br>"
        '• Try follow-ups like "Where are they?"<br>'
        "• The assistant remembers context within a session"
        "</div>",
        unsafe_allow_html=True,
    )