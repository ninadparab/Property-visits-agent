import uuid
import streamlit as st
from dotenv import load_dotenv
from langgraph.types import Command
from langgraph.checkpoint.memory import MemorySaver
from graph import build_graph

load_dotenv()

st.set_page_config(page_title="Open House Planner", page_icon="🏠", layout="wide")
st.title("🏠 Open House Visit Planner")

# ── Session init ──────────────────────────────────────────────────────────────
if "agent" not in st.session_state:
    st.session_state.agent       = build_graph(checkpointer=MemorySaver())
    st.session_state.thread_id   = str(uuid.uuid4())
    st.session_state.chat        = []
    st.session_state.interrupted = False

agent = st.session_state.agent
cfg   = {"configurable": {"thread_id": st.session_state.thread_id}}

# ── Sidebar: quick-fill form ──────────────────────────────────────────────────
with st.sidebar:
    st.header("Quick Search")

    CITIES = [
        "Bellevue", "Bothell", "Issaquah", "Kenmore", "Kirkland",
        "Mercer Island", "Newcastle", "Redmond", "Renton",
        "Sammamish", "Seattle", "Woodinville",
    ]

    city       = st.selectbox("City", CITIES, index=CITIES.index("Redmond"))
    home_addr  = st.text_input("Your home address", placeholder="500 108th Ave NE, Bellevue WA 98004")
    visit_day  = st.selectbox("Visit day", ["This Saturday", "This Sunday"])
    start_hr   = st.selectbox("Depart at", ["8:00 AM", "9:00 AM", "10:00 AM", "11:00 AM"], index=1)
    min_beds   = st.selectbox("Min bedrooms", [2, 3, 4, 5], index=1)
    max_price  = st.select_slider(
        "Max price",
        options=[500_000, 750_000, 1_000_000, 1_250_000, 1_500_000, 2_000_000, 3_000_000],
        value=1_500_000,
        format_func=lambda v: f"${v:,}",
    )
    prop_types = st.multiselect(
        "Property types",
        ["house", "condo", "townhouse"],
        default=["house", "condo", "townhouse"],
    )
    mins_per   = st.selectbox("Minutes per visit", [20, 30, 45, 60], index=1)

    if st.button("Search", type="primary", use_container_width=True):
        if not home_addr.strip():
            st.error("Enter your home address first.")
        else:
            types_str = ", ".join(prop_types) if prop_types else "house, condo, or townhouse"
            st.session_state.pending = (
                f"Find open houses in {city}, WA {visit_day.lower()}. "
                f"I'll leave at {start_hr} from {home_addr.strip()}. "
                f"{min_beds}+ beds, max ${max_price:,}, {types_str}. "
                f"Spend {mins_per} minutes at each."
            )

    st.divider()
    if st.button("↺ New search", use_container_width=True):
        for key in ["agent", "thread_id", "chat", "interrupted", "pending"]:
            st.session_state.pop(key, None)
        st.rerun()

# ── Chat history ──────────────────────────────────────────────────────────────
for msg in st.session_state.chat:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

# ── Input: sidebar button OR free-text box ────────────────────────────────────
user_text = st.chat_input("Or describe your search here…") or st.session_state.pop("pending", None)

if user_text:
    st.session_state.chat.append({"role": "user", "content": user_text})
    with st.chat_message("user"):
        st.markdown(user_text)

    with st.chat_message("assistant"):
        box = st.empty()

        inputs = (
            Command(resume=user_text)
            if st.session_state.interrupted
            else {
                "messages": [], "raw_input": user_text,
                "open_houses": [], "travel_matrix": {}, "schedules": [],
                "unschedulable": [], "error": None, "next_step": None,
            }
        )

        collected = []
        with st.spinner("Planning your visits…"):
            for chunk in agent.stream(inputs, cfg, stream_mode="updates"):
                for node, update in chunk.items():
                    if node == "__interrupt__":
                        continue
                    if update.get("error"):
                        collected.append(f"⚠️ {update['error']}")
                    for m in update.get("messages", []):
                        content = m.get("content") if isinstance(m, dict) else getattr(m, "content", None)
                        role    = m.get("role")    if isinstance(m, dict) else getattr(m, "type",    None)
                        if content and role in ("assistant", "ai"):
                            collected.append(content)

        snap = agent.get_state(cfg)
        st.session_state.interrupted = bool(snap.next)
        if snap.next:
            for task in snap.tasks:
                for itr in task.interrupts:
                    collected.append(itr.value)

        reply = "\n\n".join(collected) or "Done."
        box.markdown(reply)
        st.session_state.chat.append({"role": "assistant", "content": reply})
