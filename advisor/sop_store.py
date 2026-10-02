import re
from pathlib import Path
from typing import Literal, Union
import yaml
from pydantic import BaseModel, Field, field_validator, model_validator
from advisor.metrics import METRICS

SEVERITY_RANK = {"info": 0, "advisory": 1, "warning": 2, "danger": 3}
Op = Literal[">", ">=", "<", "<=", "==", "between"]

class Check(BaseModel):
    metric: str
    op: Op
    value: Union[float, list[float]]
    weight: float = 1.0

    @field_validator("metric")
    @classmethod
    def known_metric(cls, v):
        if v not in METRICS:
            raise ValueError(f"Unknown metric {v}")
        return v

    @model_validator(mode="after")
    def value_shape(self):
        if self.op == "between":
            if not (isinstance(self.value, list) and len(self.value) == 2):
                raise ValueError("'between' needs value: [low, high]")
        elif isinstance(self.value, list):
            raise ValueError(f"op '{self.op}' needs a single number")
        return self

class Score(BaseModel):
    of: list[Check]
    min: float | None = None
    max: float | None = None

    @model_validator(mode = "after")
    def has_bound(self):
        if self.min is None and self.max is None:
            raise ValueError("Score must have at least one of min or max")
        return self

class Block(BaseModel):
    all: list["Node"] | None = None
    any: list["Node"] | None = None
    score: Score | None = None

    @model_validator(mode="after")
    def exactly_one(self):
        set_ = [k for k in ("all", "any", "score") if getattr(self, k) is not None]
        if len(set_) != 1:
            raise ValueError(f"a condition block needs exactly one of all/any/score, got {set_ or 'none'}")
        return self

Node = Union[Block, Check]
Block.model_rebuild()

class SOP(BaseModel):
    id: str
    title: str
    category: str
    severity: Literal["info", "advisory", "warning", "danger"]
    advice: str
    when: Block
    scope: Literal["listed", "all"] = "listed"
    activities: list[str] = Field(default_factory=list)
    groups: list[str] = Field(default_factory=list)
    lead: bool = False
    all_clear: bool = False

    @field_validator("id")
    @classmethod
    def id_format(cls, v):
        if not re.fullmatch(r"[A-Z]{2,4}-\d{2}", v):
            raise ValueError(f"id '{v}' should look like EX-04")
        return v

    @field_validator("advice")
    @classmethod
    def tidy(cls, v):
        return " ".join(v.split())

    @property
    def rank(self) -> int:
        return SEVERITY_RANK[self.severity]


class Vocab(BaseModel):
    description: str
    keywords: list[str] = Field(default_factory=list)


class Policy(BaseModel):
    version: int
    activities: dict[str, Vocab]
    groups: dict[str, Vocab]
    sops: list[SOP]

    @model_validator(mode="after")
    def cross_checks(self):
        seen = set()
        for s in self.sops:
            if s.id in seen:
                raise ValueError(f"duplicate SOP id {s.id}")
            seen.add(s.id)
            for a in s.activities:
                if a not in self.activities:
                    raise ValueError(f"{s.id}: activity '{a}' is not declared under activities:")
            for g in s.groups:
                if g not in self.groups:
                    raise ValueError(f"{s.id}: group '{g}' is not declared under groups:")
            if s.scope == "listed" and not s.activities and not s.groups:
                raise ValueError(f"{s.id}: give it activities/groups, or set scope: all")
        return self

    def get(self, sop_id: str) -> SOP | None:
        return next((s for s in self.sops if s.id == sop_id), None)


DEFAULT_PATH = Path(__file__).resolve().parent.parent / "sops" / "sops.yaml"


def load_policy(path: str | Path | None = None) -> Policy:
    path = Path(path or DEFAULT_PATH)
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    return Policy.model_validate(raw)