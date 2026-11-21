"""Read dates, times and durations from a command with plain code (no tokens)."""

import re
from datetime import date, datetime, timedelta

WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
MONTHS = {
    name: index
    for index, names in enumerate(
        (
            ("january", "jan"), ("february", "feb"), ("march", "mar"), ("april", "apr"),
            ("may",), ("june", "jun"), ("july", "jul"), ("august", "aug"),
            ("september", "sep", "sept"), ("october", "oct"), ("november", "nov"),
            ("december", "dec"),
        ),
        start=1,
    )
    for name in names
}
DEFAULT_DURATION_MINUTES = 30
MAX_DURATION_MINUTES = 24 * 60

_MONTH = "|".join(sorted(MONTHS, key=len, reverse=True))
_DAY = r"\d{1,2}(?:st|nd|rd|th)?"
_DATE = re.compile(
    r"\b(?:day\s+after\s+tomorrow|today|tonight|tomorrow|(?:this|next)\s+week"
    rf"|(?:(?:this|next)\s+)?(?:{'|'.join(WEEKDAYS)})"
    r"|\d{4}-\d{2}-\d{2}"
    rf"|{_DAY}\s+(?:of\s+)?(?:{_MONTH})\b(?:\s+\d{{4}})?"
    rf"|(?:{_MONTH})\s+{_DAY}\b(?:,?\s+\d{{4}})?)\b",
    re.IGNORECASE,
)
_RANGE = re.compile(
    r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\s*(?:-|–|to|until|till)\s*"
    r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b",
    re.IGNORECASE,
)
_RANGE_24 = re.compile(
    r"\b([01]?\d|2[0-3]):([0-5]\d)\s*(?:-|–|to|until|till)\s*([01]?\d|2[0-3]):([0-5]\d)\b",
    re.IGNORECASE,
)
_TIME_12 = re.compile(r"\b(\d{1,2})(?::([0-5]\d))?\s*(am|pm)\b", re.IGNORECASE)
_TIME_24 = re.compile(r"\b([01]?\d|2[0-3]):([0-5]\d)\b")
_NAMED_TIME = re.compile(r"\b(noon|midday|midnight)\b", re.IGNORECASE)
_DURATION = re.compile(
    r"\bfor\s+(?:(half\s+an?\s+hour)|(an?\s+hour)|(\d+(?:\.\d+)?)\s*(hours?|hrs?|h|minutes?|mins?|m)\b)",
    re.IGNORECASE,
)


def find_date(text: str) -> str | None:
    match = _DATE.search(text)
    return " ".join(match.group().lower().split()) if match else None


def _to_24(hour: int, minute: int, meridiem: str) -> tuple[int, int] | None:
    if not 1 <= hour <= 12 or minute > 59:
        return None
    hour %= 12
    return (hour + 12 if meridiem.lower() == "pm" else hour, minute)


def _clock(value: tuple[int, int]) -> str:
    return f"{value[0]:02d}:{value[1]:02d}"


def find_times(text: str) -> tuple[str | None, str | None]:
    """(start, end) as HH:MM. A bare "at 3" stays unknown, because AM or PM would be a guess."""
    match = _RANGE.search(text)
    if match:
        end = _to_24(int(match[4]), int(match[5] or 0), match[6])
        if end:
            first = int(match[1]), int(match[2] or 0)
            start = _to_24(*first, match[3] or match[6])
            if start and not match[3] and start >= end:
                start = _to_24(*first, "am" if match[6].lower() == "pm" else "pm")
            if start:
                return _clock(start), _clock(end)
    match = _RANGE_24.search(text)
    if match:
        return (
            _clock((int(match[1]), int(match[2]))),
            _clock((int(match[3]), int(match[4]))),
        )
    match = _TIME_12.search(text)
    if match:
        value = _to_24(int(match[1]), int(match[2] or 0), match[3])
        if value:
            return _clock(value), None
    match = _TIME_24.search(text)
    if match:
        return _clock((int(match[1]), int(match[2]))), None
    match = _NAMED_TIME.search(text)
    if match:
        return ("00:00" if match[1].lower() == "midnight" else "12:00"), None
    return None, None


def find_duration(text: str) -> int | None:
    match = _DURATION.search(text)
    if not match:
        return None
    if match[1]:
        return 30
    if match[2]:
        return 60
    amount = float(match[3])
    minutes = round(amount * 60) if match[4].lower().startswith("h") else round(amount)
    return minutes if 0 < minutes <= MAX_DURATION_MINUTES else None


def strip_when(text: str) -> str:
    """Remove dates, times and durations, leaving the words that describe the event."""
    for pattern in (_DURATION, _RANGE, _RANGE_24, _TIME_12, _TIME_24, _NAMED_TIME, _DATE):
        text = pattern.sub(" ", text)
    return " ".join(text.split())


def resolve_date(text: str, today: date) -> date | None:
    value = text.strip().lower()
    if value in ("today", "tonight", "this week"):
        return today
    if value == "tomorrow":
        return today + timedelta(days=1)
    if value == "day after tomorrow":
        return today + timedelta(days=2)
    if value == "next week":
        return today + timedelta(days=7 - today.weekday())
    words = value.split()
    if words[-1] in WEEKDAYS:
        target = WEEKDAYS.index(words[-1])
        ahead = (target - today.weekday()) % 7
        if words[0] == "next":  # the named day in the following calendar week
            return today + timedelta(days=7 - today.weekday() + target)
        if words[0] == "this":
            return today + timedelta(days=ahead)
        return today + timedelta(days=ahead or 7)
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            return date.fromisoformat(value)
        parts = re.findall(r"[a-z]+|\d+", re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", value))
        parts = [p for p in parts if p != "of"]
        month = next(MONTHS[p] for p in parts if p in MONTHS)
        numbers = [int(p) for p in parts if p.isdigit()]
        day = numbers[0]
        if len(numbers) > 1:
            return date(numbers[1], month, day)
        candidate = date(today.year, month, day)
        return candidate if candidate >= today else date(today.year + 1, month, day)
    except (StopIteration, ValueError, IndexError):
        return None


def at_clock(day: date, clock: str, tzinfo) -> datetime:  # type: ignore[no-untyped-def]
    hour, minute = (int(part) for part in clock.split(":"))
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=tzinfo)
