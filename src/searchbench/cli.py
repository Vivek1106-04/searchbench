import asyncio
import os
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import httpx
import typer
from google import genai
from google.genai import types

from searchbench.config import Config, load_config
from searchbench.judge import (
    GenerateContent,
    Judge,
    JudgeSummary,
    Pair,
    judge_snapshot,
    judgment_cache,
    snapshot_pairs,
)
from searchbench.label import LABELS_PER_CATEGORY, label_sample, run_labeling
from searchbench.models import Task, load_tasks
from searchbench.runner import RunSummary, run_snapshot, snapshot_id
from searchbench.vendors.base import SearchVendor
from searchbench.vendors.exa import ExaVendor
from searchbench.vendors.firecrawl import FirecrawlVendor
from searchbench.vendors.tavily import TavilyVendor

VendorFactory = Callable[[httpx.AsyncClient, str, int], SearchVendor]

VENDORS: dict[str, VendorFactory] = {
    "tavily": TavilyVendor,
    "exa": ExaVendor,
    "firecrawl": FirecrawlVendor,
}

app = typer.Typer(no_args_is_help=True, add_completion=False)


@app.callback()
def main() -> None:
    """Benchmark web search APIs for LLM agents."""


def api_key_env(vendor: str) -> str:
    return f"{vendor.upper()}_API_KEY"


def require_env(names: list[str]) -> dict[str, str]:
    missing = [name for name in names if not os.environ.get(name, "").strip()]
    if missing:
        raise typer.BadParameter(f"missing or empty environment variable(s): {', '.join(missing)}")
    return {name: os.environ[name].strip() for name in names}


def require_keys(vendors: list[str]) -> dict[str, str]:
    for vendor in vendors:
        if vendor not in VENDORS:
            raise typer.BadParameter(f"config.toml names unknown vendor {vendor!r}")
    env = require_env([api_key_env(v) for v in vendors])
    return {v: env[api_key_env(v)] for v in vendors}


def git_short_sha() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "--short=7", "HEAD"], capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


async def _run(
    tasks: list[Task], config: Config, keys: dict[str, str], snapshot_dir: Path
) -> RunSummary:
    async with httpx.AsyncClient(timeout=config.run.request_timeout_s) as client:
        vendors = [
            VENDORS[name](client, key, config.run.max_content_chars) for name, key in keys.items()
        ]
        return await run_snapshot(tasks, vendors, config, snapshot_dir)


@app.command()
def run(
    config_path: Annotated[Path, typer.Option("--config")] = Path("config.toml"),
    suite: Annotated[Path, typer.Option()] = Path("suites/web_v1/tasks.jsonl"),
    results: Annotated[Path, typer.Option()] = Path("results"),
    snapshot: Annotated[str | None, typer.Option(help="Resume this snapshot id.")] = None,
) -> None:
    """Query every vendor for every task and store the responses in a snapshot."""
    config = load_config(config_path)
    keys = require_keys(list(config.vendors))
    tasks = load_tasks(suite)
    snap = snapshot or snapshot_id(datetime.now(UTC), git_short_sha())
    summary = asyncio.run(_run(tasks, config, keys, results / snap))
    typer.echo(
        f"snapshot {snap}: {summary.calls} calls, {summary.cached} cached, "
        f"{summary.errors} errors, {summary.spend_usd:.4f} USD estimated spend"
    )
    if summary.stopped_by_budget:
        typer.echo(f"stopped: spend reached max_spend_usd = {config.run.max_spend_usd}", err=True)
        raise typer.Exit(code=2)


VERTEX_RETRY = types.HttpRetryOptions(attempts=5, http_status_codes=[429, 500, 502, 503, 504])


def vertex_models() -> GenerateContent:
    env = require_env(["GOOGLE_CLOUD_PROJECT", "GOOGLE_CLOUD_LOCATION"])
    client = genai.Client(
        vertexai=True,
        project=env["GOOGLE_CLOUD_PROJECT"],
        location=env["GOOGLE_CLOUD_LOCATION"],
        http_options=types.HttpOptions(retry_options=VERTEX_RETRY),
    )
    return client.aio.models


@app.command()
def judge(
    snapshot: Annotated[str, typer.Option(help="Snapshot id to judge.")],
    config_path: Annotated[Path, typer.Option("--config")] = Path("config.toml"),
    suite: Annotated[Path, typer.Option()] = Path("suites/web_v1/tasks.jsonl"),
    results: Annotated[Path, typer.Option()] = Path("results"),
) -> None:
    """Judge every stored response with both judge models, plus the order-bias sample."""
    config = load_config(config_path)
    snapshot_dir = results / snapshot
    pairs: list[Pair] = snapshot_pairs(
        load_tasks(suite),
        list(config.vendors),
        snapshot_dir,
        config.run.k,
        config.run.max_content_chars,
    )
    if not pairs:
        raise typer.BadParameter(f"no stored responses in {snapshot_dir}; run `bench run` first")
    models = vertex_models()
    cfg = config.judge

    def make(model: str) -> Judge:
        return Judge(models, model, cfg.seed, cfg.temperature, judgment_cache(snapshot_dir, model))

    summary: JudgeSummary = asyncio.run(
        judge_snapshot(
            pairs, make(cfg.primary_model), make(cfg.second_model), cfg.concurrency, cfg.seed
        )
    )
    typer.echo(
        f"snapshot {snapshot}: {summary.pairs} pairs judged by {cfg.primary_model} and "
        f"{cfg.second_model}, {summary.reversed} re-judged in reverse order"
    )


@app.command()
def label(
    snapshot: Annotated[str, typer.Option(help="Snapshot id whose responses are labelled.")],
    config_path: Annotated[Path, typer.Option("--config")] = Path("config.toml"),
    suite: Annotated[Path, typer.Option()] = Path("suites/web_v1/tasks.jsonl"),
    results: Annotated[Path, typer.Option()] = Path("results"),
    labels: Annotated[Path, typer.Option()] = Path("suites/web_v1/human_labels.jsonl"),
) -> None:
    """Label a blind, category-stratified sample of (task, vendor) pairs by hand."""
    config = load_config(config_path)
    pairs = snapshot_pairs(
        load_tasks(suite),
        list(config.vendors),
        results / snapshot,
        config.run.k,
        config.run.max_content_chars,
    )
    if not pairs:
        raise typer.BadParameter(f"no stored responses in {results / snapshot}")
    sample = label_sample(pairs, LABELS_PER_CATEGORY, config.judge.seed)
    recorded = run_labeling(
        sample, snapshot, labels, ask=lambda message: str(typer.prompt(message)), show=typer.echo
    )
    typer.echo(f"recorded {recorded} label(s) in {labels}")
