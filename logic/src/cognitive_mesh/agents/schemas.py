"""Typed outputs exchanged between employees (LangChain structured output)."""

from typing import Literal

from pydantic import BaseModel, Field

LINKEDIN_MAX_CHARS = 3000  # LINKEDIN_CREATE_LINKED_IN_POST `commentary` limit


class CommandIntent(BaseModel):
    action: Literal["linkedin_post", "unsupported"] = Field(
        description="linkedin_post if the user wants a LinkedIn post written/published; "
        "otherwise unsupported."
    )
    topic: str = Field(description="What the post should be about, in the user's terms.")
    instructions: str = Field(
        default="", description="Tone, length, audience or other constraints the user gave."
    )
    explicit_research: bool = Field(
        default=False,
        description="True ONLY if the user explicitly asks for research, e.g. 'research', "
        "'in-depth research', 'investigate', 'deep dive', or mentions @research. "
        "A plain request to write or publish a post is False.",
    )


class Insight(BaseModel):
    claim: str = Field(description="One specific, factual finding.")
    source_ids: list[str] = Field(
        min_length=1, description="Ids (e.g. 'S3') of the sources that support this claim."
    )


class ResearchFindings(BaseModel):
    summary: str = Field(description="2-4 sentence overview of what is new and why it matters.")
    insights: list[Insight] = Field(min_length=1, max_length=8)


class PostDraft(BaseModel):
    text: str = Field(
        max_length=LINKEDIN_MAX_CHARS,
        description="The post body only, ready to publish. Plain text, no markdown. "
        "Do NOT put hashtags in the body; they go in `hashtags`.",
    )
    hashtags: list[str] = Field(
        default_factory=list, max_length=5, description="Without the # sign, e.g. AgenticAI."
    )
    source_ids: list[str] = Field(
        min_length=1,
        max_length=6,
        description="Ids of the 2-6 strongest sources this post relies on.",
    )


class ReviewVerdict(BaseModel):
    approved: bool = Field(description="True only if the post is ready to show the user.")
    accuracy: int = Field(ge=1, le=5, description="Claims match the cited sources.")
    relevance: int = Field(ge=1, le=5, description="On topic for the request and audience.")
    quality: int = Field(ge=1, le=5, description="Clear, engaging, professional.")
    formatting: int = Field(ge=1, le=5, description="Readable on LinkedIn; length is right.")
    issues: list[str] = Field(default_factory=list)
    required_changes: list[str] = Field(
        default_factory=list, description="Concrete edits needed before approval."
    )


def full_post_text(draft: PostDraft) -> str:
    """Exactly what would be published: body, a blank line, then #hashtags."""
    tags = " ".join(f"#{h.lstrip('#')}" for h in draft.hashtags if h.strip("# "))
    body = draft.text.strip()
    return f"{body}\n\n{tags}" if tags else body
