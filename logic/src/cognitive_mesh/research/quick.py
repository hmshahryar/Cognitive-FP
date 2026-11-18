"""Quick search: the default when the user didn't explicitly ask for research.

One news search + one web search, straight through the SearchProvider, and findings
built directly from the results. No model calls, so it's fast and cheap; every
insight still cites a source the search actually returned.
"""

import re

from cognitive_mesh.agents.schemas import CommandIntent, Insight, ResearchFindings
from cognitive_mesh.research.providers import SearchProvider
from cognitive_mesh.research.sources import SourceRegistry

HITS_PER_SEARCH = 5
MAX_SUMMARY_CHARS = 1200

# An explicit mention always selects the Research employee, regardless of the model.
EXPLICIT_RESEARCH_MENTION = re.compile(r"(?<!\w)@research\b", re.IGNORECASE)


def wants_deep_research(command: str, intent: CommandIntent) -> bool:
    return intent.explicit_research or bool(EXPLICIT_RESEARCH_MENTION.search(command))


def _strip_citation_markers(text: str) -> str:
    return re.sub(r"\s*\[\d+\]", "", text).strip()


class QuickResearcher:
    def __init__(self, provider: SearchProvider) -> None:
        self._provider = provider

    async def research(
        self, intent: CommandIntent, profile: str, registry: SourceRegistry
    ) -> ResearchFindings:
        news = await self._provider.search_news(intent.topic, "w")
        web = await self._provider.search_web(intent.topic)

        insights: list[Insight] = []
        for hit in news[:HITS_PER_SEARCH]:
            source = registry.add(hit, self._provider.name)
            claim = f"{hit.title}" + (f": {hit.snippet}" if hit.snippet else "")
            insights.append(Insight(claim=claim, source_ids=[source.id]))

        web_ids = [registry.add(h, self._provider.name).id for h in web.hits[:HITS_PER_SEARCH]]
        answer = _strip_citation_markers(web.answer)
        if answer and web_ids:
            insights.append(Insight(claim=answer[:MAX_SUMMARY_CHARS], source_ids=web_ids))

        summary = answer[:MAX_SUMMARY_CHARS] or f"Recent coverage of {intent.topic}."
        # The pipeline rejects empty/uncited findings; an empty search fails cleanly there.
        return ResearchFindings.model_construct(summary=summary, insights=insights[:8])
