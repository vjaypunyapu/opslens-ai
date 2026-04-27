"""OpsLens AI SDK exceptions."""


class OpsLensError(Exception):
    """Base exception for all OpsLens SDK errors."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class AuthenticationError(OpsLensError):
    """Raised when the API key is missing or invalid (401/403)."""


class RateLimitError(OpsLensError):
    """Raised when the API rate limit is exceeded (429)."""
