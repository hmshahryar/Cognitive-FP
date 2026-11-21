"""Deterministic content-quality checks, run on every draft before the AI reviewer.

Blocking findings (invented numbers, quotes or personal experience) can never reach the
approval card. Style findings go back to the writer and, if they survive the revision
rounds, are shown to the user. This measures credibility and readability; it is not an
AI-detector score and is not designed to evade one.
"""

import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from typing import Any

from cognitive_mesh.agents.schemas import PostDraft

MAX_HASHTAGS = 3
MAX_EMOJIS = 2
MAX_EXCLAMATIONS = 1
MAX_PARAGRAPH_CHARS = 600
PLAIN_NUMBER_THRESHOLD = 100  # smaller bare numbers are usually counts ("3 reasons")

_NUMBER = r"\d[\d,]*(?:\.\d+)?"
_ANY_NUMBER = re.compile(rf"(?<![\w.])(?:{_NUMBER})")
_STAT = re.compile(
    rf"(?<![\w.])(?P<currency>[$€£]\s?)?(?P<value>{_NUMBER})"
    r"(?P<unit>\s?(?:%|percent\b|x\b|k\b|m\b|bn\b|b\b|thousand\b|million\b|billion\b|trillion\b))?",
    re.IGNORECASE,
)
_QUOTE = re.compile(r"[“\"]([^”\"\n]{10,500})[”\"]")
_EMOJI = re.compile("[\U0001f300-\U0001faff☀-➿]")
_WORD = re.compile(r"[a-z0-9']+")

_EXPERIENCE = re.compile(
    r"\bin my (?:\d+ )?(?:years?|experience|career|work|last role|previous role)\b"
    r"|\bi(?:'ve| have) (?:seen|built|worked|led|helped|spent|learned|watched|shipped"
    r"|managed|launched|founded|coached|interviewed)\b"
    r"|\bwhen i (?:was|worked|started|joined|built|led|founded)\b"
    r"|\bi (?:recently|once|remember|built|led|founded|launched|worked|shipped)\b"
    r"|\b(?:my|our) (?:team|company|clients?|customers?|startup|employer)\b"
    r"|\bwe (?:built|shipped|launched|deployed|helped)\b"
    r"|\blast (?:week|month|year),? i\b",
    re.IGNORECASE,
)

# The model talking about its task instead of writing the post.
_META_COMMENTARY = re.compile(
    r"\bi (?:shouldn't|should not) include\b|\bextra text\b|\bas an ai\b"
    r"|\bthe user (?:asked|wants|requested)\b|\brevised draft\b"
    r"|\bhere(?:'s| is) the (?:revised |final |updated )?(?:post|draft)\b"
)

_PLACEHOLDER = re.compile(r"\[[^\]\n]{1,60}\]|\{\{?[^}\n]{1,60}\}\}?|<(?:your|name|date)[^>\n]*>", re.IGNORECASE)

STOCK_PHRASES = (
    "in today's fast-paced",
    "in today's digital",
    "in an ever-changing",
    "ever-evolving",
    "game-changer",
    "game changer",
    "delve into",
    "unlock the power",
    "unlock the potential",
    "harness the power",
    "navigate the complexities",
    "paradigm shift",
    "revolutionize",
    "at the end of the day",
    "let that sink in",
    "the future is here",
    "in conclusion",
    "it's important to note",
    "here's the thing",
)

GENERIC_ENDINGS = (
    "what are your thoughts",
    "what do you think",
    "share your thoughts",
    "let me know in the comments",
    "drop a comment",
    "follow for more",
    "agree?",
)


@dataclass
class QualityReport:
    blocking: list[str] = field(default_factory=list)
    style: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _normalize(text: str) -> str:
    text = text.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    return re.sub(r"\s+", " ", text).strip().lower()


def _canonical(raw: str) -> str:
    value = raw.replace(",", "")
    return value.rstrip("0").rstrip(".") if "." in value else value


def all_numbers(text: str) -> set[str]:
    return {_canonical(m.group()) for m in _ANY_NUMBER.finditer(text)}


def stat_numbers(text: str) -> set[str]:
    """Numbers that read as facts: percentages, money, multiples, large values, years."""
    found = set()
    for match in _STAT.finditer(text):
        value = _canonical(match["value"])
        if not value:
            continue
        if match["currency"] or match["unit"] or float(value) >= PLAIN_NUMBER_THRESHOLD:
            found.add(value)
    return found


def has_personal_experience(text: str) -> bool:
    return bool(_EXPERIENCE.search(_normalize(text)))


def _blocking(draft: PostDraft, evidence: dict[str, str], user_text: str) -> list[str]:
    problems: list[str] = []
    meta = _META_COMMENTARY.search(_normalize(draft.text))
    if meta:
        problems.append(
            f'The post contains writing-process text ("{meta.group()}"). '
            "Return only the finished post."
        )
    user_numbers = all_numbers(user_text)
    claim_numbers = [
        (
            all_numbers(claim.text),
            all_numbers(" ".join(evidence.get(i, "") for i in claim.source_ids)),
        )
        for claim in draft.claims
    ]
    for number in sorted(stat_numbers(draft.text)):
        supported = number in user_numbers or any(
            number in stated and number in backed for stated, backed in claim_numbers
        )
        if not supported:
            problems.append(
                f"The figure {number} is not backed by a listed claim and its cited sources. "
                "Remove it, or use the exact figure and cite the source that states it."
            )

    cited = {*draft.source_ids, *(i for c in draft.claims for i in c.source_ids)}
    corpus = _normalize(" ".join(evidence.get(i, "") for i in cited) + " " + user_text)
    for quote in _QUOTE.findall(draft.text):
        words = _normalize(quote).strip(" .,;:!?")
        if len(words.split()) >= 4 and words not in corpus:
            problems.append(
                f'The quotation "{quote[:80]}" does not appear in the cited sources. '
                "Remove it or paraphrase without quotation marks."
            )

    if not has_personal_experience(user_text):
        match = _EXPERIENCE.search(_normalize(draft.text))
        if match:
            problems.append(
                f'"{match.group()}" implies a personal experience the author never gave. '
                "Rewrite it as analysis or observation about the sources."
            )
    return problems


def _style(draft: PostDraft) -> list[str]:
    text = draft.text.strip()
    normalized = _normalize(text)
    notes = [f'Replace the stock phrase "{p}".' for p in STOCK_PHRASES if p in normalized]

    if len([h for h in draft.hashtags if h.strip("# ")]) > MAX_HASHTAGS:
        notes.append(f"Use at most {MAX_HASHTAGS} hashtags, only ones that are truly relevant.")
    if len(_EMOJI.findall(text)) > MAX_EMOJIS:
        notes.append("Remove the decorative emojis.")
    if text.count("!") > MAX_EXCLAMATIONS:
        notes.append("Remove the exclamation marks; let the point carry the energy.")
    if "**" in text or re.search(r"^#{1,6}\s", text, re.MULTILINE):
        notes.append("Remove markdown: LinkedIn shows it as literal characters.")

    paragraphs = [p for p in re.split(r"\n\s*\n", text) if p.strip()]
    if any(len(p) > MAX_PARAGRAPH_CHARS for p in paragraphs):
        notes.append("Break long paragraphs into shorter ones for LinkedIn readability.")
    if paragraphs and any(e in _normalize(paragraphs[-1]) for e in GENERIC_ENDINGS):
        notes.append("Replace the generic closing question with a specific final point.")

    sentences = [s for s in re.split(r"(?<=[.!?])\s+|\n+", text) if s.strip()]
    openers = Counter(
        words[0]
        for s in sentences
        if (words := _WORD.findall(s.lower())) and words[0] not in {"the", "a", "an"}
    )
    repeated_openers = [w for w, n in openers.items() if n >= 3]
    if repeated_openers:
        notes.append(f'Vary sentence openings: "{repeated_openers[0]}" starts 3 or more sentences.')

    words = _WORD.findall(normalized)
    phrases = Counter(" ".join(words[i : i + 4]) for i in range(len(words) - 3))
    repeated = [p for p, n in phrases.items() if n >= 2]
    if repeated:
        notes.append(f'Avoid repeating the phrase "{repeated[0]}".')
    return notes


def email_problems(subject: str, body: str, user_text: str) -> list[str]:
    """Blocking checks for AI-written email: no placeholders and no invented figures."""
    text = f"{subject}\n{body}"
    problems = []
    placeholder = _PLACEHOLDER.search(text)
    if placeholder:
        problems.append(f'Remove the placeholder "{placeholder.group()}".')
    meta = _META_COMMENTARY.search(_normalize(text))
    if meta:
        problems.append(f'Remove the writing-process text "{meta.group()}".')
    allowed = all_numbers(user_text)
    for number in sorted(stat_numbers(text)):
        if number not in allowed:
            problems.append(f"Remove the figure {number}: the user did not provide it.")
    return problems


def check_post(draft: PostDraft, evidence: dict[str, str], user_text: str = "") -> QualityReport:
    """`evidence` maps source id to its text; `user_text` is what the user told us directly."""
    return QualityReport(blocking=_blocking(draft, evidence, user_text), style=_style(draft))
