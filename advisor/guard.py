"""Checks the LLM's reply before showing it to the user.

The LLM should only rephrase the matched SOPs, not add its own facts.
So we check three things:
  - every number in the reply comes from the forecast or the SOPs
  - every SOP id it mentions actually matched
  - it doesn't say "safe" when a warning or danger SOP matched

If any check fails, we throw the reply away and use the template instead.
"""
import re
from dataclasses import dataclass, field
from advisor.matcher import Hit

NUM = re.compile(r"(?<![A-Za-z])-?\d+(?:\.\d+)?")
SOP_ID = re.compile(r"\b[A-Z]{2,4}-\d{2}\b")
REASSURING = re.compile(
    r"\b(perfectly safe|completely safe|totally safe|safe to go|you(?:'re| are) fine|no risk|nothing to worry|go ahead)\b",
    re.I,
)

@dataclass
class Verdict:
    ok: bool
    problems: list[str] = field(default_factory=list)

def numbers(text:str) -> set[float]:
    return {abs(float(n)) for n in NUM.findall(SOP_ID.sub(" ",text))}

def verify(reply: str, hits: list[Hit], allowed_source: str) -> Verdict:
    problems = []

    allowed = numbers(allowed_source)
    allowed |= {float(round(a)) for a in allowed}   # "72.3 mm" may be written as "72 mm"
    stray = sorted(n for n in numbers(reply) if not any(abs(n - a) < 0.051 for a in allowed))
    if stray:
        problems.append(f"numbers not in the forecast or SOPs: {', '.join(f'{n:g}' for n in stray)}")

    valid_ids = {h.sop.id for h in hits}
    cited = set(SOP_ID.findall(reply))
    if cited - valid_ids:
        problems.append(f"cites SOPs that did not match: {', '.join(sorted(cited - valid_ids))}")
    if hits and hits[0].sop.id not in cited:
        problems.append(f"does not mention the primary SOP {hits[0].sop.id}")

    if hits and hits[0].sop.rank >= 2 and REASSURING.search(reply):
        problems.append(f"reassuring language next to a {hits[0].sop.severity}-level SOP")

    return Verdict(not problems, problems)