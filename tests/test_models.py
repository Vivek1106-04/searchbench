from datetime import date
from typing import Any

import pytest
from pydantic import ValidationError

from searchbench.models import Judgment, Result, Task, VendorResponse


def task_fields(**overrides: Any) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "id": "web_v1_001",
        "category": "factual",
        "query": "postgresql default port",
        "gold_answer": "5432",
        "gold_evidence": "PostgreSQL listens on TCP port 5432 by default.",
        "gold_domains": ["postgresql.org"],
        "as_of": "2026-11-19",
        "time_sensitive": False,
    }
    return fields | overrides


def test_task_parses_jsonl_fields() -> None:
    task = Task.model_validate(task_fields())

    assert task.as_of == date(2026, 11, 19)
    assert task.gold_domains == ["postgresql.org"]


@pytest.mark.parametrize(
    "overrides",
    [
        {"query": "   "},
        {"gold_answer": ""},
        {"gold_domains": []},
        {"gold_domains": [""]},
        {"category": "opinion"},
        {"as_of": "not a date"},
        {"surprise": 1},
    ],
)
def test_task_rejects_invalid_fields(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        Task.model_validate(task_fields(**overrides))


def test_task_is_immutable() -> None:
    task = Task.model_validate(task_fields())

    with pytest.raises(ValidationError):
        task.query = "changed"


def test_vendor_response_defaults_to_no_error() -> None:
    response = VendorResponse(
        results=[Result(url="https://a.example", title="A", content="text")],
        latency_ms=12.5,
        cost_usd=0.008,
        raw={"results": [{"url": "https://a.example"}]},
    )

    assert response.error is None


@pytest.mark.parametrize("field", ["latency_ms", "cost_usd"])
def test_vendor_response_rejects_negative_measurements(field: str) -> None:
    fields: dict[str, Any] = {"results": [], "latency_ms": 1.0, "cost_usd": 0.0, "raw": None}

    with pytest.raises(ValidationError):
        VendorResponse.model_validate(fields | {field: -1.0})


def test_judgment_result_index_is_one_based() -> None:
    assert Judgment(supported="no").supporting_result_index is None
    assert Judgment(supported="yes", supporting_result_index=1, quote="5432").quote == "5432"
    with pytest.raises(ValidationError):
        Judgment(supported="yes", supporting_result_index=0, quote="5432")
