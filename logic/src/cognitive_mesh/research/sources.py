"""Sources the research actually returned. Agents may only cite ids from here."""

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class Source:
    id: str  # "S1", "S2", ... stable within one execution
    url: str
    title: str
    publisher: str | None = None
    published_at: str | None = None
    snippet: str | None = None
    provider: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SearchHit:
    url: str
    title: str
    publisher: str | None = None
    published_at: str | None = None
    snippet: str | None = None


class SourceRegistry:
    """Deduplicates hits by URL and hands out short ids for citation."""

    def __init__(self) -> None:
        self._by_url: dict[str, Source] = {}
        self._by_id: dict[str, Source] = {}

    def add(self, hit: SearchHit, provider: str) -> Source:
        url = hit.url.strip()
        if url in self._by_url:
            return self._by_url[url]
        source = Source(
            id=f"S{len(self._by_id) + 1}",
            url=url,
            title=hit.title.strip() or url,
            publisher=hit.publisher,
            published_at=hit.published_at,
            snippet=hit.snippet,
            provider=provider,
        )
        self._by_url[url] = source
        self._by_id[source.id] = source
        return source

    def get(self, source_id: str) -> Source | None:
        return self._by_id.get(source_id)

    def ids(self) -> set[str]:
        return set(self._by_id)

    def all(self) -> list[Source]:
        return list(self._by_id.values())

    def unknown(self, ids: list[str]) -> list[str]:
        """Ids that were never returned by a search (i.e. invented citations)."""
        return [i for i in ids if i not in self._by_id]

    def __len__(self) -> int:
        return len(self._by_id)

    def to_dicts(self) -> list[dict[str, Any]]:
        return [s.to_dict() for s in self._by_id.values()]

    @classmethod
    def from_dicts(cls, rows: list[dict[str, Any]]) -> "SourceRegistry":
        """Rebuild with the SAME ids (used when a checkpointed run resumes)."""
        registry = cls()
        for row in rows:
            source = Source(**row)
            registry._by_url[source.url] = source
            registry._by_id[source.id] = source
        return registry
