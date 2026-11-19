import re
from typing import Protocol

from searchbench.models import VendorResponse

_WHITESPACE = re.compile(r"\s+")


class SearchVendor(Protocol):
    name: str

    async def search(self, query: str, k: int) -> VendorResponse: ...


def normalize_content(text: str, max_chars: int) -> str:
    return _WHITESPACE.sub(" ", text).strip()[:max_chars]
