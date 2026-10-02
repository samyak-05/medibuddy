import operator
from typing import Annotated, Any, TypedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from advisor import compose as C
from advisor import intent as I
from advisor.guard import verify
from advisor.llm import LLM
from advisor.matcher import match, relevant_sops
from advisor.sop_store import Policy, load_policy
from advisor.weather import (OpenMeteo, Place, WeatherError, build_snapshot,
                             snapshot_from_dict, snapshot_to_dict)


class State(TypedDict, total=False):
    message: str
    memory: dict
    log: Annotated[list, operator.add]
    intent: dict
    place: dict | None
    snapshot: dict | None
    matched: list[str]
    draft: str | None
    reply: str
    outcome: str
    problems: list[str]
    error: str | None
    path: list[str]


TURN_DEFAULTS = dict(intent={}, place=None, snapshot=None, matched=[], draft=None, reply="", outcome="", problems=[], error=None, path=[])


def build(policy: Policy | None = None, llm: LLM | None = None, weather=None, checkpointer=None):
    """`policy` may be a Policy or a zero-arg callable returning one. The app
    passes a callable that re-reads sops.yaml, so edits apply on the next
    message without a restart."""
    get_policy = policy if callable(policy) else (lambda p=policy or load_policy(): p)
    weather = weather or OpenMeteo()

    def step(state, name):
        return state.get("path", []) + [name]

    # ---------------- nodes --------------------

    def understand(state: State):
        mem = state.get("memory") or {}
        it = I.parse(llm, get_policy(), state["message"], mem)
        return {"intent": it.model_dump(), "path": step(state, "understand")}

    def locate(state: State):
        mem = state.get("memory") or {}
        name, country = state["intent"]["location"], state["intent"].get("country")
        same = (mem.get("location", "").lower() == name.lower()
                and (mem.get("country") or "").lower() == (country or "").lower())
        if mem.get("place") and same:
            return {"place": mem["place"], "path": step(state, "locate(cached)")}
        try:
            place = weather.geocode(name, country)
        except WeatherError as e:
            return {"error": str(e), "path": step(state, "locate")}
        return {"place": place.__dict__, "path": step(state, "locate")}

    def fetch(state: State):
        p = Place(**state["place"])
        try:
            raw = weather.forecast(p.lat, p.lon)
            snap = build_snapshot(p, raw, state["intent"]["window"])
            if p.candidates > 1 and not state["intent"].get("country"):
                # Ambiguous name: say which one we picked instead of hiding it.
                extra = (f"several places are called '{state['intent']['location']}'; "
                         f"I used {p.label}. Tell me the country if you meant another")
                snap.note = f"{snap.note}. {extra}" if snap.note else extra
        except WeatherError as e:
            return {"error": str(e), "path": step(state, "fetch")}
        except (KeyError, TypeError, ValueError) as e:
            return {"error": f"unexpected forecast format ({type(e).__name__})", "path": step(state, "fetch")}
        return {"snapshot": snapshot_to_dict(snap), "path": step(state, "fetch")}

    def do_match(state: State):
        it, snap = state["intent"], state["snapshot"]
        hits = match(get_policy(), it["activity"], it["groups"], snap["metrics"])
        return {"matched": [h.sop.id for h in hits], "path": step(state, "match")}

    def _hits(state):
        it = state["intent"]
        return match(get_policy(), it["activity"], it["groups"], state["snapshot"]["metrics"])

    def do_compose(state: State):
        snap = snapshot_from_dict(state["snapshot"])
        hits, it = _hits(state), state["intent"]
        if llm is None:
            return {"reply": C.template(hits, snap), "outcome": "advice:template", "path": step(state, "compose")}
        try:
            draft = C.llm_compose(llm, hits, snap, it["activity"], it["groups"])
        except Exception as e:
            return {"reply": C.template(hits, snap), "outcome": "advice:template",
                    "problems": [f"model call failed: {type(e).__name__}"], "path": step(state, "compose")}
        return {"draft": draft, "path": step(state, "compose")}

    def do_verify(state: State):
        snap = snapshot_from_dict(state["snapshot"])
        hits = _hits(state)
        v = verify(state["draft"], hits, C.allowed_text(hits, snap))
        if v.ok:
            return {"reply": state["draft"], "outcome": "advice:llm", "path": step(state, "verify")}
        return {"reply": C.template(hits, snap), "outcome": "advice:template(rejected draft)",
                "problems": v.problems, "path": step(state, "verify")}

    def no_guidance(state: State):
        it = state["intent"]
        if not it.get("in_scope"):
            reply = ("Sorry, that's outside what I can help with. I only give weather-safety guidance "
                     "for outdoor plans, like cycling, travel, picnics or taking kids or pets out, "
                     "and I don't have a policy that covers this question.")
            return {"reply": reply, "outcome": "no_sop:out_of_scope", "path": step(state, "no_guidance")}

        snap = snapshot_from_dict(state["snapshot"])
        checked = [s.id for s in relevant_sops(get_policy(), it["activity"], it["groups"])]
        what = (it["activity"] or "this activity").replace("_", " ")
        if it["activity"] is None:
            body = ("We don't have a policy for this particular activity, so I won't give specific "
                    "advice. None of our general outdoor alerts (heavy rain, thunderstorms, heat, "
                    "high UV) are active for this window.")
        else:
            body = (f"None of our {what} rules are triggered by this forecast, and there's no policy "
                    f"that covers this exact situation, so I don't have specific guidance to give.")
        reply = (f"{C.header(snap)}\n\n{body}\n\n{C.forecast_line(snap)}\n\n"
                 f"Based on: no SOP matched (checked {', '.join(checked)})")
        return {"reply": reply, "outcome": "no_sop:no_match", "path": step(state, "no_guidance")}

    def cant_understand(state: State):
        return {"reply": "Sorry, I couldn't process your question right now (the language model is "
                         "unavailable). I won't guess, so please try again in a moment.",
                "outcome": "model_unavailable", "path": step(state, "cant_understand")}

    def ask_location(state: State):
        return {"reply": "Which city or town is this for? I need it to pull the forecast.",
                "outcome": "needs_location", "path": step(state, "ask_location")}

    def cant_forecast(state: State):
        loc = state["intent"].get("location")
        reply = (f"I couldn't get a reliable forecast for {loc} right now ({state['error']}). "
                 f"I won't guess at the conditions, so I can't give safety advice for this one. "
                 f"Please try again in a few minutes, or check the spelling of the place.")
        return {"reply": reply, "outcome": "no_forecast", "path": step(state, "cant_forecast")}

    def remember(state: State):
        mem = dict(state.get("memory") or {})
        it = state.get("intent") or {}
        if it.get("in_scope"):
            for k in ("activity", "groups", "window"):
                if it.get(k):
                    mem[k] = it[k]
        if it.get("in_scope") and it.get("location"):
            # keep the name even if lookup failed, so "try again" works;
            # drop the old coordinates so we never reuse another city's.
            mem["location"] = it["location"]
            mem["country"] = it.get("country")
            mem["place"] = state.get("place")
        if state.get("snapshot"):
            mem["observed_at"] = state["snapshot"]["observed_at"]
        entry = {"q": state["message"], "outcome": state["outcome"], "sops": state.get("matched", []),
                 "window": it.get("window"), "location": it.get("location")}
        return {"memory": mem, "log": [entry], "path": step(state, "remember")}

    # ------------------ routing --------------------

    def after_understand(state: State):
        it = state["intent"]
        if it["parser"] == "failed":
            return "cant_understand"
        if not it["in_scope"]:
            return "no_guidance"
        return "locate" if it.get("location") else "ask_location"

    def ok_or_fail(nxt):
        return lambda state: "cant_forecast" if state.get("error") else nxt

    def after_match(state: State):
        return "compose" if state["matched"] else "no_guidance"

    def after_compose(state: State):
        return "remember" if state.get("reply") else "verify"

    g = StateGraph(State)
    for name, fn in [("understand", understand), ("locate", locate), ("fetch", fetch),
                     ("match", do_match), ("compose", do_compose), ("verify", do_verify),
                     ("no_guidance", no_guidance), ("ask_location", ask_location),
                     ("cant_understand", cant_understand), ("cant_forecast", cant_forecast),
                     ("remember", remember)]:
        g.add_node(name, fn)

    g.add_edge(START, "understand")
    g.add_conditional_edges("understand", after_understand,["cant_understand", "no_guidance", "ask_location", "locate"])
    g.add_conditional_edges("locate", ok_or_fail("fetch"), ["fetch", "cant_forecast"])
    g.add_conditional_edges("fetch", ok_or_fail("match"), ["match", "cant_forecast"])
    g.add_conditional_edges("match", after_match, ["compose", "no_guidance"])
    g.add_conditional_edges("compose", after_compose, ["verify", "remember"])
    for n in ("verify", "no_guidance", "ask_location", "cant_forecast", "cant_understand"):
        g.add_edge(n, "remember")
    g.add_edge("remember", END)
    return g.compile(checkpointer=checkpointer or MemorySaver())


def run_turn(graph, thread_id: str, message: str) -> dict[str, Any]:
    out = graph.invoke({"message": message, **TURN_DEFAULTS},config={"configurable": {"thread_id": thread_id}})
    return out