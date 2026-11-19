import hashlib
import json
from pathlib import Path

from pydantic import BaseModel


def cache_key(*parts: object) -> str:
    canonical = json.dumps(parts, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode()).hexdigest()


class JsonlCache[M: BaseModel]:
    """Append-only JSONL store, one {"key", "value"} object per line.

    A crash mid-write leaves a final line with no newline; it is dropped on load
    so the interrupted call is simply made again.
    """

    def __init__(self, path: Path, model: type[M]) -> None:
        self._path = path
        self._model = model
        self._entries: dict[str, M] = {}
        if path.exists():
            self._load()

    def _load(self) -> None:
        data = self._path.read_bytes()
        complete = data[: data.rfind(b"\n") + 1]
        if len(complete) < len(data):
            self._path.write_bytes(complete)
        for lineno, line in enumerate(complete.decode().splitlines(), start=1):
            try:
                record = json.loads(line)
                self._entries[record["key"]] = self._model.model_validate(record["value"])
            except (ValueError, KeyError, TypeError) as exc:
                raise ValueError(f"{self._path}:{lineno}: corrupt cache line") from exc

    def get(self, key: str) -> M | None:
        return self._entries.get(key)

    def put(self, key: str, value: M) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps({"key": key, "value": value.model_dump(mode="json")}, ensure_ascii=True)
        with self._path.open("a", encoding="ascii") as f:
            f.write(line + "\n")
        self._entries[key] = value

    def values(self) -> list[M]:
        return list(self._entries.values())

    def __len__(self) -> int:
        return len(self._entries)
