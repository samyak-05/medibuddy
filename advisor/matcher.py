#Docstring

"""This module decides which SOPs apply.

If conflict arises then,
 1. General SOPs are preferred over specific SOPs.
 2. Then they are ranked by severity, highest first.
 3. `all_clear` SOPs are dropped if anything advisory-or-worse also
     matched, so we never say "no adverse signals" next to a warning.
 4. Show the top SOP plus up to MAX_SECONDARY (2) more.
"""

from dataclasses import dataclass, field
from advisor.sop_store import SOP, Block,Check, Policy

MAX_SECONDARY = 2

_OPS = {
    ">": lambda a, b:a > b,
    ">=": lambda a, b:a >= b,
    "<": lambda a, b:a < b,
    "<=": lambda a, b:a <= b,
    "==": lambda a, b:a == b,
    "between": lambda a, b: b[0] <= a <= b[1],
}

@dataclass
class Evidence:
    metric: str
    op: str
    threshold : object
    actual: float
    passed: bool
    weight: float | None = None

@dataclass
class Hit:
    sop: SOP
    evidence: list[Evidence] = field(default_factory=list)
    score: float | None = None

    @property
    def triggers(self) -> list[Evidence]:
        """Checks that actually passed; these are the numbers worth quoting"""
        return [e for e in self.evidence if e.passed]

def evaluate(node, metrics: dict) -> tuple[bool, list[Evidence], float | None]:
    if isinstance(node, Check):
        actual = metrics[node.metric]
        ok = _OPS[node.op](actual, node.value)
        return ok, [Evidence(node.metric, node.op, node.value, actual, ok)], None

    block: Block = node
    if block.score is not None:
        sc = block.score
        ev, total = [], 0.0
        for c in sc.of:
            ok = _OPS[c.op](metrics[c.metric], c.value)
            total += c.weight if ok else 0
            ev.append(Evidence(c.metric, c.op, c.value, metrics[c.metric], ok, c.weight))
        ok = (sc.min is None or total >= sc.min) and (sc.max is None or total <= sc.max)
        return ok, ev, total

    children = block.all if block.all is not None else block.any
    results = [evaluate(c, metrics) for c in children]
    ok = all(r[0] for r in results) if block.all is not None else any(r[0] for r in results)
    ev = [e for r in results for e in r[1]]
    return ok, ev, None


def applies_to(sop: SOP, activity: str | None, groups: list[str]) -> bool:
    if sop.groups and not set(sop.groups) & set(groups):
        return False
    if sop.scope == "all":
        return True
    if sop.activities:
        return activity in sop.activities
    return bool(sop.groups)  # group-only rule, group already matched above


def match(policy: Policy, activity: str | None, groups: list[str], metrics: dict) -> list[Hit]:
    hits = []
    for sop in policy.sops:
        if not applies_to(sop, activity, groups):
            continue
        ok, ev, score = evaluate(sop.when, metrics)
        if ok:
            hits.append(Hit(sop, ev, score))

    if any(h.sop.rank >= 1 and not h.sop.all_clear for h in hits):
        hits = [h for h in hits if not h.sop.all_clear]

    order = {s.id: i for i, s in enumerate(policy.sops)}
    hits.sort(key=lambda h: (not h.sop.lead, -h.sop.rank, order[h.sop.id]))
    return hits[: 1 + MAX_SECONDARY]


def relevant_sops(policy: Policy, activity: str | None, groups: list[str]) -> list[SOP]:
    """SOPs that *could* apply to this question, whatever the weather."""
    return [s for s in policy.sops if applies_to(s, activity, groups)]
