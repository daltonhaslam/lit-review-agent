"""Exceptions shared by the literature-source clients."""
from __future__ import annotations

from typing import Optional

import httpx


class SourceRetrievalError(Exception):
    """A source failed for a reason other than "no matches".

    Raised by a client's ``search()`` when the request ultimately fails (rate limit,
    server error, network) after retries. Distinguishes a degraded source from a
    genuine zero-hit query so callers never record an outage as ``0 results``.

    ``partial`` carries any articles retrieved before the failure so callers can keep
    them; ``status_code`` is the final HTTP status when the failure was an HTTP error.
    """

    def __init__(
        self,
        source: str,
        message: str,
        *,
        status_code: Optional[int] = None,
        partial: Optional[list] = None,
    ):
        super().__init__(message)
        self.source = source
        self.status_code = status_code
        self.partial = list(partial or [])

    @classmethod
    def from_exception(
        cls,
        source: str,
        exc: Exception,
        partial: Optional[list] = None,
        *,
        hint: str = "",
    ) -> "SourceRetrievalError":
        """Wrap a failed request: HTTP errors keep their status; others keep type + text.

        ``hint`` is appended to the message (source-specific remediation advice).
        """
        status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
        msg = f"HTTP {status} after retries" if status is not None else f"{type(exc).__name__}: {exc}"
        return cls(source, msg + hint, status_code=status, partial=partial)
