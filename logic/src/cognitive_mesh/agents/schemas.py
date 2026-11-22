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


class PostClaim(BaseModel):
    text: str = Field(description="One factual claim made in the post, including any figure.")
    source_ids: list[str] = Field(
        min_length=1, description="Ids of the sources that directly state this claim."
    )


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
        default_factory=list,
        max_length=6,
        description="Ids of the 2-6 strongest sources this post relies on. "
        "Empty when no research was provided.",
    )
    claims: list[PostClaim] = Field(
        default_factory=list,
        max_length=12,
        description="Internal fact-check list, never published: every factual claim in the "
        "post with the sources that state it.",
    )


class EmailDraft(BaseModel):
    """Recipients are deliberately absent: only the user's command can set them."""

    subject: str = Field(max_length=200, description="One clear subject line, plain text.")
    body: str = Field(
        max_length=10000,
        description="The complete plain-text email, with no placeholders or signature name.",
    )


class ManagerDecision(BaseModel):
    """Which specialist should handle a message. Code validates this before anything runs."""

    target: Literal["calendar", "gmail", "linkedin", "sheets", "clarify", "unsupported"] = Field(
        description="The specialist for this request, clarify if unclear, unsupported otherwise."
    )
    action: Literal[
        "none", "list", "free", "create", "reschedule", "cancel", "write_email",
        "forward_email", "trash_email", "write_post", "research_post", "sheet_list",
        "sheet_read", "sheet_create", "sheet_append", "sheet_update",
    ] = Field(default="none", description="The specialist's action.")
    sheet_name: str = Field(
        default="", max_length=120, description="For sheets: the spreadsheet name the user gave."
    )
    columns: list[str] = Field(
        default_factory=list,
        max_length=26,
        description="For sheet_create: column names, with spelling corrected.",
    )
    rows: list[list[str]] = Field(
        default_factory=list,
        max_length=50,
        description="For sheet_append or sheet_create: values copied exactly from the user's "
        "message.",
    )
    use_active_item: bool = Field(
        default=False,
        description="True when the message refers to the active sheet or event in the chat state.",
    )
    cell: str = Field(default="", max_length=10, description="For sheet_update: a cell like B2.")
    value: str = Field(
        default="", max_length=500, description="For sheet_update: the exact new value."
    )
    title: str = Field(
        default="",
        max_length=120,
        description="For calendar create: a clean event title with spelling corrected.",
    )
    date_phrase: str = Field(
        default="",
        max_length=40,
        description="The target date as the user said it, with spelling corrected and no added "
        "year, e.g. '24 november' or 'tomorrow'. Empty if the user gave no date.",
    )
    source_date_phrase: str = Field(
        default="",
        max_length=40,
        description="For reschedule or cancel: the event's current date, if the user gave one.",
    )
    all_day: bool = Field(
        default=False,
        description="True when marking a whole day, such as a deadline, holiday or due date, "
        "with no clock time.",
    )
    event_query: str = Field(
        default="",
        max_length=100,
        description="For reschedule or cancel: words from the user that identify the event.",
    )
    question: str = Field(
        default="", max_length=240, description="For clarify: one short question for the user."
    )


class ConversationSummary(BaseModel):
    summary: str = Field(
        max_length=1500,
        description="Compact factual summary of goals, decisions, preferences and open items.",
    )


class ReviewVerdict(BaseModel):
    approved: bool = Field(description="True only if the post is ready to show the user.")
    accuracy: int = Field(ge=1, le=5, description="Claims match the cited sources.")
    relevance: int = Field(ge=1, le=5, description="On topic for the request and audience.")
    quality: int = Field(ge=1, le=5, description="Substantive, useful insight.")
    formatting: int = Field(ge=1, le=5, description="Readable on LinkedIn; length is right.")
    professionalism: int = Field(ge=1, le=5, description="Credible, measured professional tone.")
    readability: int = Field(ge=1, le=5, description="Fluent, with natural sentence variation.")
    originality: int = Field(ge=1, le=5, description="A specific angle, not a generic summary.")
    repetition: int = Field(ge=1, le=5, description="5 means no repeated ideas or phrasing.")
    natural_language: int = Field(
        ge=1, le=5, description="5 means free of formulaic, template-like language."
    )
    unsupported_claims: list[str] = Field(
        default_factory=list, description="Claims in the post that the sources do not support."
    )
    issues: list[str] = Field(default_factory=list)
    required_changes: list[str] = Field(
        default_factory=list, description="Concrete edits needed before approval."
    )

    def meets_bar(self) -> bool:
        """Code-enforced bar, so a lenient `approved=true` cannot pass a weak draft."""
        scores = (
            self.relevance, self.quality, self.formatting, self.professionalism,
            self.readability, self.originality, self.repetition, self.natural_language,
        )
        return (
            self.approved
            and self.accuracy >= 4
            and min(scores) >= 3
            and not self.unsupported_claims
        )


def full_post_text(draft: PostDraft) -> str:
    """Exactly what would be published: body, a blank line, then #hashtags."""
    tags = " ".join(f"#{h.lstrip('#')}" for h in draft.hashtags if h.strip("# "))
    body = draft.text.strip()
    return f"{body}\n\n{tags}" if tags else body
