class ResearchFailedError(Exception):
    """Research produced no verifiable, cited findings."""


class CitationError(Exception):
    """A draft cites sources the research never returned, or breaks a hard limit."""
