import functools
import json
import re
from pathlib import Path

import httpx
import pytest
import respx
from typer.testing import CliRunner

from searchbench import cli, runner
from searchbench.cli import app
from searchbench.vendors import exa, firecrawl, tavily

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

    code, output = invoke_run(tmp_path, suite)

    assert code != 0
    assert "EXA_API_KEY" in output
    assert "FIRECRAWL_API_KEY" in output
    assert not (tmp_path / "results").exists()


def test_unknown_vendor_in_config_is_rejected(keys: None, suite: Path, tmp_path: Path) -> None:
    config = (
        REPO / "config.toml"
    ).read_text() + "\n[vendors.bing]\nconcurrency = 1\nrequests_per_second = 1.0\n"
    (tmp_path / "config.toml").write_text(config)

    result = CliRunner().invoke(
        app, ["run", "--config", str(tmp_path / "config.toml"), "--suite", str(suite)]
    )

    assert result.exit_code != 0
    assert "unknown vendor 'bing'" in result.output


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
