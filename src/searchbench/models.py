from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StringConstraints

Category = Literal["factual", "technical_docs", "fresh", "multi_hop", "long_tail"]
Support = Literal["yes", "partial", "no"]

NonEmpty = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Task(Frozen):
    id: NonEmpty
    category: Category
    query: NonEmpty
    gold_answer: NonEmpty
    gold_evidence: NonEmpty
    gold_domains: list[NonEmpty] = Field(min_length=1)
    as_of: date
    time_sensitive: bool


class Result(Frozen):
    url: str
    title: str
    content: str


class VendorResponse(Frozen):
    results: list[Result]
    latency_ms: float = Field(ge=0)
    cost_usd: float = Field(ge=0)
    raw: JsonValue
    error: str | None = None


class Judgment(Frozen):
    supported: Support
    supporting_result_index: int | None = Field(default=None, ge=1)
    quote: str | None = None
