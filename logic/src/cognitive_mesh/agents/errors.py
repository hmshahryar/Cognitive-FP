class ResearchFailedError(Exception):
    """Research produced no verifiable, cited findings."""


class CitationError(Exception):
    """A draft keeps failing a blocking check (sources, figures, quotes, experience, length)."""
