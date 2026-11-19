from pathlib import Path

import pytest
from pydantic import ValidationError

from searchbench.config import load_config

REPO_CONFIG = Path(__file__).parent.parent / "config.toml"


def test_repo_config_loads() -> None:
    config = load_config(REPO_CONFIG)

    assert config.run.k == 5
    assert config.run.max_content_chars == 2000
    assert {"tavily", "exa", "firecrawl"} <= config.vendors.keys()


def test_invalid_value_is_rejected(tmp_path: Path) -> None:
    broken = REPO_CONFIG.read_text().replace("k = 5", "k = 0")
    path = tmp_path / "config.toml"
    path.write_text(broken)

    with pytest.raises(ValidationError):
        load_config(path)


def test_missing_section_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text("[run]\nk = 5\n")

    with pytest.raises(ValidationError):
        load_config(path)
