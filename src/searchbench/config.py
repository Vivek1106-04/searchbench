import tomllib
from pathlib import Path

from pydantic import Field

from searchbench.models import Frozen, NonEmpty


class RunConfig(Frozen):
    k: int = Field(ge=1)
    max_content_chars: int = Field(ge=1)
    max_spend_usd: float = Field(gt=0)
    max_attempts: int = Field(ge=1)
    request_timeout_s: float = Field(gt=0)


class VendorLimits(Frozen):
    concurrency: int = Field(ge=1)
    requests_per_second: float = Field(gt=0)


class JudgeConfig(Frozen):
    primary_model: NonEmpty
    second_model: NonEmpty
    seed: int
    temperature: float = Field(ge=0)
    concurrency: int = Field(ge=1)


class Config(Frozen):
    run: RunConfig
    vendors: dict[str, VendorLimits] = Field(min_length=1)
    judge: JudgeConfig


def load_config(path: Path) -> Config:
    with path.open("rb") as f:
        return Config.model_validate(tomllib.load(f))
