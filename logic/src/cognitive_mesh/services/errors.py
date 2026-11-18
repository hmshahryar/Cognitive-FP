"""Domain errors. One handler in main.py turns them into HTTP responses."""


class DomainError(Exception):
    status_code = 400
    default_detail = "Bad request"

    def __init__(self, detail: str | None = None) -> None:
        self.detail = detail or self.default_detail
        super().__init__(self.detail)


class NotFoundError(DomainError):
    # Also used for other workspaces' resources so their existence isn't revealed.
    status_code = 404
    default_detail = "Not found"


class ForbiddenError(DomainError):
    status_code = 403
    default_detail = "You are not allowed to do this"


class ConflictError(DomainError):
    """The resource isn't in a state that allows this (e.g. cancel a finished task)."""

    status_code = 409
    default_detail = "This action is not allowed in the current state"


class ApprovalMismatchError(ConflictError):
    default_detail = "The content you approved is not the current version; review it again"


class ApprovalInvalidError(ConflictError):
    default_detail = "This approval is no longer valid; a new approval is required"


class ConfigurationError(DomainError):
    status_code = 500
    default_detail = "Server is not configured for this action"
