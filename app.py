import uuid

import streamlit as st
from dotenv import load_dotenv
from pydantic import ValidationError

from advisor import llm as llm_mod
from advisor.graph import build, run_turn
from advisor.metrics import METRICS, fmt
from advisor.sop_store import load_policy

load_dotenv()
st.set_page_config(page_title="Weather Advisor", page_icon="⛅", layout="centered")


@st.cache_resource
def get_graph():
    # policy is passed as a loader, so sops.yaml is re-read on every message:
    # edit the file, send the next question, the new rule is live.
    return build(policy=load_policy, llm=llm_mod.from_env())


if llm_mod.from_env() is None:
    st.error("GROQ_API_KEY is not set. Add it to your .env file and restart the app.")
    st.stop()

if "thread" not in st.session_state:
    st.session_state.thread = str(uuid.uuid4())
    st.session_state.turns = []

with st.sidebar:
    st.subheader("Session")
    if st.button("New session", use_container_width=True):
        st.session_state.thread = str(uuid.uuid4())
        st.session_state.turns = []
        st.rerun()

    st.subheader("Active policies")
    try:
        policy = load_policy()
        for s in policy.sops:
            st.markdown(f"`{s.id}` {s.title} · *{s.severity}*")
    except ValidationError as e:
        st.error(f"sops.yaml is invalid:\n\n{e}")

st.title("Outdoor weather advisor")
st.caption("Ask whether a plan is safe given today's weather. Answers come only from written policies.")


def show_trace(out):
    it = out.get("intent") or {}
    st.markdown(f"**Path:** {' → '.join(out.get('path', []))}  \n"
                f"**Outcome:** `{out.get('outcome')}`  \n"
                f"**Understood as:** activity=`{it.get('activity')}`, groups=`{it.get('groups')}`, "
                f"place=`{it.get('location')}`, window=`{it.get('window')}` (parser: {it.get('parser')})")
    snap = out.get("snapshot")
    if snap:
        st.markdown("**Forecast numbers used**")
        st.table({METRICS[k][1]: fmt(k, v) for k, v in snap["metrics"].items()})
    if out.get("problems"):
        st.warning("Model draft rejected, template sent instead: " + "; ".join(out["problems"]))


for turn in st.session_state.turns:
    with st.chat_message("user"):
        st.write(turn["q"])
    with st.chat_message("assistant"):
        st.text(turn["out"]["reply"])
        with st.expander("Why did it say that?"):
            show_trace(turn["out"])

q = st.chat_input("e.g. Is it safe to cycle to work in Bhopal today?")
if q:
    with st.chat_message("user"):
        st.write(q)
    with st.chat_message("assistant"):
        try:
            with st.spinner("Checking the forecast…"):
                out = run_turn(get_graph(), st.session_state.thread, q)
        except ValidationError as e:
            st.error(f"The policy file has an error, so I won't answer until it's fixed:\n\n{e}")
            st.stop()
        st.text(out["reply"])
        with st.expander("Why did it say that?"):
            show_trace(out)
    st.session_state.turns.append({"q": q, "out": out})
