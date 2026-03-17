class TollgateError(Exception):
    """Base exception for all Tollgate errors."""

    pass


class TollgateDenied(TollgateError):  # noqa: N818
    """Raised when a tool call is explicitly denied by policy."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(f"Tool call denied: {reason}")


class TollgateApprovalDenied(TollgateError):  # noqa: N818
    """Raised when a human-in-the-loop approval is denied."""

    def __init__(self, reason: str = "Approval denied by human."):
        self.reason = reason
        super().__init__(reason)


class TollgateDeferred(TollgateError):  # noqa: N818
    """Raised when a tool call is deferred (e.g., waiting for async approval)."""

    def __init__(self, approval_id: str):
        self.approval_id = approval_id
        super().__init__(f"Tool call deferred. Approval ID: {approval_id}")


class TollgateRateLimited(TollgateError):  # noqa: N818
    """Raised when a tool call is rejected due to rate limiting."""

    def __init__(self, reason: str, retry_after: float | None = None):
        self.reason = reason
        self.retry_after = retry_after
        msg = f"Rate limited: {reason}"
        if retry_after is not None:
            msg += f" (retry after {retry_after:.1f}s)"
        super().__init__(msg)


class TollgateConstraintViolation(TollgateError):  # noqa: N818
    """Raised when tool parameters violate manifest constraints."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(f"Constraint violation: {reason}")
