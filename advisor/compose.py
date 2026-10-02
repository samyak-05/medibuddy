"""Writing the reply.

Two paths produce the same kind of answer:
  - template(): deterministic, no model. Used as the fallback whenever the model's draft 
    fails verification or the model call fails.".
  - llm_compose(): the model rewrites the matched SOPs into friendlier text.

The model never sees the user's raw message here, only the structured facts
below. And whatever it writes goes through guard.verify() before a user
sees it. The "Based on:" trail at the bottom is always added by code, so a
citation can't be dropped or invented by the model.
"""

import json
from advisor.llm import LLM
from advisor.matcher import Hit
from advisor.metrics import METRICS, fmt
from advisor.weather import WeatherSnapshot

HEADLINE = ["temp_max", "feels_like_max", "precip_prob_max", "rain_window", "wind_gust_max", "uv_max"]
OP_WORDS = {">": "above", ">=": "at or above", "<": "below", "<=": "at or below", "==": "equal to", "between": "between"}

def _threshold(e) -> str:
    if e.op == "between":
        return f"{fmt(e.metric, e.threshold[0])}–{fmt(e.metric, e.threshold[1])}"
    return fmt(e.metric, e.threshold)

def why_lines(hit: Hit) -> list[str]:
    if hit.score is not None:
        out = [f"comfort score {hit.score:g} (rule needs "
               + " and ".join(x for x in [
                   f"at least {hit.sop.when.score.min:g}" if hit.sop.when.score.min is not None else "",
                   f"at most {hit.sop.when.score.max:g}" if hit.sop.when.score.max is not None else ""] if x)
               + ")"]
        for e in hit.evidence:
            mark = "+" if e.passed else "·"
            out.append(f"{mark} {METRICS[e.metric][1]} {fmt(e.metric, e.actual)} "
                       f"(wants {OP_WORDS[e.op]} {_threshold(e)}, weight {e.weight:g})")
        return out
    seen, out = set(), []
    for e in hit.triggers:          # one line per metric, first (strictest) rule wins
        if e.metric in seen:
            continue
        seen.add(e.metric)
        out.append(f"{METRICS[e.metric][1]} {fmt(e.metric, e.actual)} — {OP_WORDS[e.op]} {_threshold(e)}")
    return out


def header(snap: WeatherSnapshot) -> str:
    h = f"{snap.place.label} · {snap.window_label} ({snap.start}–{snap.end})"
    return h + (f"\nNote: {snap.note}." if snap.note else "")

def forecast_line(snap: WeatherSnapshot) -> str:
    """Plain forecast facts, shown even when no SOP matched."""
    return "Forecast for this window: " + ", ".join(
        f"{METRICS[k][1]} {fmt(k, snap.metrics[k])}" for k in HEADLINE) + "."

def trail(hits: list[Hit], snap: WeatherSnapshot) -> str:
    ids = ", ".join(f"{h.sop.id} ({h.sop.severity})" for h in hits)
    return f"Based on: {ids} · Open-Meteo forecast, issued {snap.observed_at} local time"

def template(hits: list[Hit], snap: WeatherSnapshot) -> str:
    parts = [header(snap), ""]
    for i, h in enumerate(hits):
        tag = "Main concern" if i == 0 and len(hits) > 1 else ("Also" if i else "Guidance")
        parts.append(f"{tag} — {h.sop.title} [{h.sop.id}, {h.sop.severity}]")
        parts.append(h.sop.advice)
        parts.append("Why: " + "; ".join(why_lines(h)))
        parts.append("")
    parts.append(trail(hits, snap))
    return "\n".join(parts)

SYSTEM = """You turn weather-safety policy matches into a short reply for a user.

Hard rules:
- Use ONLY the facts in the JSON. Do not add advice that is not in an SOP's
  "advice" field. Do not add tips of your own.
- Every number you write must appear in the JSON exactly (you may drop a
  trailing ".0"). Never estimate, convert units, or round differently.
- Mention each SOP by its id in square brackets, e.g. [TW-01].
- Keep the order given. If the first SOP has "lead": true, open with it.
- Match the severity. Never call conditions safe or fine if any SOP is
  "warning" or "danger".
- 3 to 6 sentences, plain text, no headings, no bullet points, no emoji.
"""

def llm_compose(llm: LLM, hits: list[Hit], snap: WeatherSnapshot, activity: str | None, groups: list[str]) -> str:
    payload = {
        "place": snap.place.label,
        "window": snap.window_label,
        "note": snap.note or None,
        "activity": activity,
        "with": groups,
        "forecast": {METRICS[k][1]: fmt(k, snap.metrics[k]) for k in HEADLINE},
        "sops": [{
            "id": h.sop.id, "title": h.sop.title, "severity": h.sop.severity,
            "lead": h.sop.lead, "advice": h.sop.advice, "because": why_lines(h),
        } for h in hits],
    }
    body = llm.complete(SYSTEM, json.dumps(payload, ensure_ascii=False, indent=1)).strip()
    return f"{header(snap)}\n\n{body}\n\n{trail(hits, snap)}"

def allowed_text(hits: list[Hit], snap: WeatherSnapshot) -> str:
    """Everything the model was shown. Numbers in the reply must come from here."""
    bits = [header(snap), trail(hits, snap)]
    bits += [fmt(k, v) for k, v in snap.metrics.items()]
    for h in hits:
        bits += [h.sop.id, h.sop.title, h.sop.advice, *why_lines(h)]
    return "\n".join(bits)