"""Search providers behind one interface. Add Tavily/Exa/etc. by implementing it.

- ComposioSearchProvider (default): keyless, via the user's Composio session.
- GeminiGroundingProvider: Google Search grounding (requires Gemini billing).
"""

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from langchain_core.language_models import BaseChatModel

from cognitive_mesh.integrations.gateway import IntegrationGateway
from cognitive_mesh.research.sources import SearchHit

Recency = Literal["d", "w", "m", "y"]


@dataclass(frozen=True)
class WebAnswer:
    answer: str
    hits: list[SearchHit] = field(default_factory=list)


class SearchProvider(Protocol):
    name: str

    async def search_news(self, query: str, recency: Recency = "w") -> list[SearchHit]: ...

    async def search_web(self, query: str) -> WebAnswer: ...


# ---------- Composio Search ----------
def parse_composio_news(data: Any) -> list[SearchHit]:
    """Shape verified live: data.news_results[{title, link, source, published_at, snippet}]."""
    results = (data or {}).get("news_results") or []
    return [
        SearchHit(
            url=r["link"],
            title=r.get("title") or r["link"],
            publisher=r.get("source"),
            published_at=r.get("published_at") or r.get("date"),
            snippet=r.get("snippet"),
        )
        for r in results
        if isinstance(r, dict) and r.get("link")
    ]


def parse_composio_web(data: Any) -> WebAnswer:
    """Shape verified live: data.answer + data.citations[{url, title, author, publishedDate}]."""
    data = data or {}
    hits = [
        SearchHit(
            url=c["url"],
            title=c.get("title") or c["url"],
            publisher=c.get("author"),
            published_at=c.get("publishedDate"),
        )
        for c in data.get("citations") or []
        if isinstance(c, dict) and c.get("url")
    ]
    return WebAnswer(answer=str(data.get("answer") or ""), hits=hits)


class ComposioSearchProvider:
    name = "composio_search"
    TOOLKIT = "composio_search"

    def __init__(self, gateway: IntegrationGateway, user_id: str) -> None:
        self._gateway = gateway
        self._user_id = user_id

    async def search_news(self, query: str, recency: Recency = "w") -> list[SearchHit]:
        data = await self._gateway.execute_read_only(
            user_id=self._user_id,
            toolkit_slug=self.TOOLKIT,
            tool_slug="COMPOSIO_SEARCH_NEWS",
            arguments={"query": query, "when": recency},
        )
        return parse_composio_news(data)

    async def search_web(self, query: str) -> WebAnswer:
        data = await self._gateway.execute_read_only(
            user_id=self._user_id,
            toolkit_slug=self.TOOLKIT,
            tool_slug="COMPOSIO_SEARCH_WEB",
            arguments={"query": query},
        )
        return parse_composio_web(data)


# ---------- Gemini Google Search grounding ----------
def parse_grounding(response_metadata: dict[str, Any]) -> list[SearchHit]:
    """langchain-google-genai puts candidate.grounding_metadata in response_metadata."""
    chunks = (response_metadata.get("grounding_metadata") or {}).get("grounding_chunks") or []
    hits = []
    for chunk in chunks:
        web = (chunk or {}).get("web") or {}
        if web.get("uri"):
            hits.append(SearchHit(url=web["uri"], title=web.get("title") or web["uri"]))
    return hits


class GeminiGroundingProvider:
    """Requires a billed Gemini project (grounding is not on the free tier)."""

    name = "gemini_google_search"

    def __init__(self, model: BaseChatModel) -> None:
        self._model = model.bind_tools([{"google_search": {}}])

    async def _ask(self, prompt: str) -> WebAnswer:
        message = await self._model.ainvoke(prompt)
        return WebAnswer(answer=message.text, hits=parse_grounding(message.response_metadata))

    async def search_news(self, query: str, recency: Recency = "w") -> list[SearchHit]:
        window = {"d": "day", "w": "week", "m": "month", "y": "year"}[recency]
        return (await self._ask(f"Recent news from the past {window} about: {query}")).hits

    async def search_web(self, query: str) -> WebAnswer:
        return await self._ask(query)
