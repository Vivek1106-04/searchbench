import functools
import json
import re
from pathlib import Path

import httpx
import pytest
import respx
import typer
from google.genai import types
from typer.testing import CliRunner

from searchbench import cli, runner
from searchbench.cli import app
from searchbench.vendors import exa, firecrawl, tavily
from tests.snapshots import task, verdict, write_snapshot

REPO = Path(__file__).parent.parent
KEYS = ("TAVILY_API_KEY", "EXA_API_KEY", "FIRECRAWL_API_KEY")


def test_help_lists_the_tool() -> None:
    result = CliRunner().invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "Benchmark web search APIs" in result.output


@pytest.fixture
def keys(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in KEYS:
        monkeypatch.setenv(name, "secret")


@pytest.fixture
def suite(tmp_path: Path) -> Path:
    lines = (REPO / "suites" / "web_v1" / "tasks.jsonl").read_text().splitlines()[:2]
    path = tmp_path / "tasks.jsonl"
    path.write_text("\n".join(lines) + "\n")
    return path


def mock_vendors() -> None:
    respx.post(tavily.SEARCH_URL).mock(
        return_value=httpx.Response(
            200, json={"results": [{"url": "u", "title": "t", "content": "c"}]}
        )
    )
    respx.post(exa.SEARCH_URL).mock(
        return_value=httpx.Response(200, json={"results": [{"url": "u", "title": "t"}]})
    )
    respx.post(firecrawl.SEARCH_URL).mock(return_value=httpx.Response(503))


def usage_error(args: list[str]) -> str:
    """The BadParameter message, unrendered: Rich wraps and colours it differently in CI."""
    result = CliRunner().invoke(app, args, standalone_mode=False)
    assert isinstance(result.exception, typer.BadParameter), result.output
    return result.exception.format_message()


def invoke_run(tmp_path: Path, suite: Path, *extra: str) -> tuple[int, str]:
    result = CliRunner().invoke(
        app,
        [
            "run",
            "--config",
            str(REPO / "config.toml"),
            "--suite",
            str(suite),
            "--results",
            str(tmp_path / "results"),
            "--snapshot",
            "snap",
            *extra,
        ],
    )
    return result.exit_code, result.output


@respx.mock
def test_run_writes_one_cache_file_per_vendor(
    keys: None, suite: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mock_vendors()
    instant_runner(monkeypatch)

    code, output = invoke_run(tmp_path, suite)

    assert code == 0, output
    assert "snapshot snap: 6 calls, 0 cached, 2 errors" in output
    responses = tmp_path / "results" / "snap" / "responses"
    assert sorted(p.name for p in responses.iterdir()) == [
        "exa.jsonl",
        "firecrawl.jsonl",
        "tavily.jsonl",
    ]
    failed = [json.loads(line) for line in (responses / "firecrawl.jsonl").read_text().splitlines()]
    assert all(r["value"]["error"] == "HTTP 503 after 3 attempt(s)" for r in failed)


@respx.mock
def test_rerun_of_same_snapshot_makes_no_calls(
    keys: None, suite: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mock_vendors()
    instant_runner(monkeypatch)
    invoke_run(tmp_path, suite)
    calls_before = len(respx.calls)

    code, output = invoke_run(tmp_path, suite)

    assert code == 0, output
    assert "0 calls, 6 cached" in output
    assert len(respx.calls) == calls_before


@respx.mock
def test_budget_stop_exits_nonzero(
    keys: None, suite: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = (
        (REPO / "config.toml").read_text().replace("max_spend_usd = 10.0", "max_spend_usd = 0.001")
    )
    (tmp_path / "config.toml").write_text(config)
    mock_vendors()
    instant_runner(monkeypatch)

    result = CliRunner().invoke(
        app,
        [
            "run",
            "--config",
            str(tmp_path / "config.toml"),
            "--suite",
            str(suite),
            "--results",
            str(tmp_path / "results"),
            "--snapshot",
            "snap",
        ],
    )

    assert result.exit_code == 2
    assert "stopped: spend reached max_spend_usd" in result.output


def test_empty_key_is_rejected_before_any_call(
    keys: None, suite: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FIRECRAWL_API_KEY", "  ")
    monkeypatch.delenv("EXA_API_KEY")

    message = usage_error(
        [
            "run",
            "--config",
            str(REPO / "config.toml"),
            "--suite",
            str(suite),
            "--results",
            str(tmp_path / "results"),
            "--snapshot",
            "snap",
        ]
    )

    assert "EXA_API_KEY" in message
    assert "FIRECRAWL_API_KEY" in message
    assert not (tmp_path / "results").exists()


def test_unknown_vendor_in_config_is_rejected(keys: None, suite: Path, tmp_path: Path) -> None:
    config = (
        REPO / "config.toml"
    ).read_text() + "\n[vendors.bing]\nconcurrency = 1\nrequests_per_second = 1.0\n"
    (tmp_path / "config.toml").write_text(config)

    message = usage_error(["run", "--config", str(tmp_path / "config.toml"), "--suite", str(suite)])

    assert "unknown vendor 'bing'" in message


def test_snapshot_defaults_to_date_and_git_sha(
    keys: None, suite: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[Path] = []

    async def fake_run(tasks: object, config: object, keys: object, snapshot_dir: Path) -> object:
        seen.append(snapshot_dir)
        return runner.RunSummary()

    monkeypatch.setattr(cli, "_run", fake_run)
    monkeypatch.setattr(cli, "git_short_sha", lambda: "abc1234")

    result = CliRunner().invoke(
        app,
        [
            "run",
            "--config",
            str(REPO / "config.toml"),
            "--suite",
            str(suite),
            "--results",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0, result.output
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}_abc1234", seen[0].name)


def test_git_short_sha_reads_this_repo() -> None:
    assert re.fullmatch(r"[0-9a-f]{7}", cli.git_short_sha())


async def _no_sleep(seconds: float) -> None:
    return None


def instant_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        cli, "run_snapshot", functools.partial(runner.run_snapshot, sleep=_no_sleep)
    )


class CannedModels:
    async def generate_content(
        self, *, model: str, contents: str, config: object
    ) -> types.GenerateContentResponse:
        part = types.Part(
            text='{"supported": "no", "supporting_result_index": null, "quote": null}'
        )
        return types.GenerateContentResponse(
            candidates=[types.Candidate(content=types.Content(role="model", parts=[part]))]
        )


@respx.mock
def test_judge_command_judges_a_stored_snapshot(
    keys: None, suite: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mock_vendors()
    instant_runner(monkeypatch)
    invoke_run(tmp_path, suite)
    monkeypatch.setattr(cli, "vertex_models", CannedModels)

    result = CliRunner().invoke(
        app,
        [
            "judge",
            "--snapshot",
            "snap",
            "--config",
            str(REPO / "config.toml"),
            "--suite",
            str(suite),
            "--results",
            str(tmp_path / "results"),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "6 pairs judged by gemini-3.1-pro-preview and gemini-3.5-flash-lite" in result.output
    judgments = tmp_path / "results" / "snap" / "judgments"
    assert sorted(p.name for p in judgments.iterdir()) == [
        "gemini-3.1-pro-preview.jsonl",
        "gemini-3.5-flash-lite.jsonl",
    ]


def test_judge_command_needs_stored_responses(tmp_path: Path, suite: Path) -> None:
    message = usage_error(
        [
            "judge",
            "--snapshot",
            "none",
            "--config",
            str(REPO / "config.toml"),
            "--suite",
            str(suite),
            "--results",
            str(tmp_path),
        ]
    )

    assert "run `bench run` first" in message


def test_vertex_models_uses_vertex_with_adc_and_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    class FakeClient:
        def __init__(self, **kwargs: object) -> None:
            seen.update(kwargs)
            self.aio = _Aio()

    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "proj")
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "global")
    monkeypatch.setattr("searchbench.cli.genai.Client", FakeClient)

    models: object = cli.vertex_models()
    assert models == "models"
    assert seen["vertexai"] is True
    assert (seen["project"], seen["location"]) == ("proj", "global")
    assert "api_key" not in seen


def test_vertex_models_requires_project_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "global")

    with pytest.raises(typer.BadParameter, match="GOOGLE_CLOUD_PROJECT"):
        cli.vertex_models()


class _Aio:
    models = "models"


@respx.mock
def test_label_command_records_answers(
    keys: None, suite: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mock_vendors()
    instant_runner(monkeypatch)
    invoke_run(tmp_path, suite)
    labels = tmp_path / "human_labels.jsonl"

    result = CliRunner().invoke(
        app,
        [
            "label",
            "--snapshot",
            "snap",
            "--config",
            str(REPO / "config.toml"),
            "--suite",
            str(suite),
            "--results",
            str(tmp_path / "results"),
            "--labels",
            str(labels),
        ],
        input="y\nq\n",
    )

    assert result.exit_code == 0, result.output
    assert "recorded 1 label(s)" in result.output
    assert "https://" not in result.output
    assert len(labels.read_text().splitlines()) == 1


def test_label_command_needs_stored_responses(tmp_path: Path, suite: Path) -> None:
    message = usage_error(
        [
            "label",
            "--snapshot",
            "none",
            "--config",
            str(REPO / "config.toml"),
            "--suite",
            str(suite),
            "--results",
            str(tmp_path),
        ]
    )

    assert "no stored responses" in message


SYNTHETIC_CONFIG = """
[run]
k = 5
max_content_chars = 2000
max_spend_usd = 10.0
max_attempts = 3
request_timeout_s = 60.0

[vendors.alpha]
concurrency = 1
requests_per_second = 1.0

[vendors.beta]
concurrency = 1
requests_per_second = 1.0

[judge]
primary_model = "judge-pro"
second_model = "judge-lite"
seed = 1
temperature = 0.0
concurrency = 1
"""


def synthetic_workspace(tmp_path: Path, snapshots: dict[str, str]) -> list[str]:
    """Write config, suite and one snapshot per id with every pair judged as given."""
    tasks = [task(i) for i in range(4)] + [task(10, "fresh")]
    (tmp_path / "config.toml").write_text(SYNTHETIC_CONFIG)
    (tmp_path / "tasks.jsonl").write_text("".join(t.model_dump_json() + "\n" for t in tasks))
    for snap, supported in snapshots.items():
        judgments = {
            (v, t.id): verdict("yes" if supported == "yes" else "no", t)
            for v in ("alpha", "beta")
            for t in tasks
        }
        write_snapshot(tmp_path / "results" / snap, tasks, judgments)
    return [
        "--config",
        str(tmp_path / "config.toml"),
        "--suite",
        str(tmp_path / "tasks.jsonl"),
        "--results",
        str(tmp_path / "results"),
    ]


def test_report_command_writes_report_and_plot(tmp_path: Path) -> None:
    options = synthetic_workspace(tmp_path, {"s1": "yes"})

    result = CliRunner().invoke(
        app, ["report", "--snapshot", "s1", *options, "--labels", str(tmp_path / "none.jsonl")]
    )

    assert result.exit_code == 0, result.output
    snapshot_dir = tmp_path / "results" / "s1"
    assert (snapshot_dir / "REPORT.md").read_text().startswith("# searchbench report: s1")
    assert (snapshot_dir / "cost_vs_support.png").read_bytes().startswith(b"\x89PNG")


def test_drift_command_prints_the_comparison(tmp_path: Path) -> None:
    options = synthetic_workspace(tmp_path, {"s1": "yes", "s2": "no"})

    result = CliRunner().invoke(app, ["drift", "s1", "s2", *options])

    assert result.exit_code == 0, result.output
    assert "# Drift: s1 -> s2" in result.output
    assert "| alpha | other | 4 | -1.00 [-1.00, -1.00] | yes |" in result.output
    assert "| beta | fresh | 1 |" in result.output
