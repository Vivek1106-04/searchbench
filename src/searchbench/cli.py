import asyncio
import os
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import httpx
import typer

from searchbench.config import Config, load_config
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


def require_keys(vendors: list[str]) -> dict[str, str]:
    for vendor in vendors:
        if vendor not in VENDORS:
            raise typer.BadParameter(f"config.toml names unknown vendor {vendor!r}")
    missing = [api_key_env(v) for v in vendors if not os.environ.get(api_key_env(v), "").strip()]
    if missing:
        raise typer.BadParameter(f"missing or empty environment variable(s): {', '.join(missing)}")
    return {v: os.environ[api_key_env(v)].strip() for v in vendors}


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
