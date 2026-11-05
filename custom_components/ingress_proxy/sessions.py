"""Panel sessions: short-lived random tokens that let an app's iframe through the proxy.

The panel asks for one over Home Assistant's websocket (websocket.py), which proves who the user is. The token
then travels as a cookie on every request the iframe makes, because an iframe can't send Home Assistant's own
login. A token only opens the app it was made for, and only for as long as the panel keeps renewing it.

Tokens live in memory only, so they all end when Home Assistant restarts; the panel asks for a new one when its
websocket reconnects.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import secrets
import time

from .const import SESSION_TTL_SECONDS


@dataclass(frozen=True)
class Session:
    """Who a token was given to, and until when it works."""

    slug: str  # the one app this token opens
    is_admin: bool  # whether the user was a Home Assistant administrator when it was made
    expires: float  # on the monotonic clock


class SessionStore:
    """Hands out tokens and checks them."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        """Start empty. `clock` is only swapped by the tests."""
        self._clock = clock
        self._sessions: dict[str, Session] = {}

    def create(self, slug: str, is_admin: bool) -> str:
        """Make a new token for one app and return it."""
        self._forget_expired()
        token = secrets.token_urlsafe(32)
        self._sessions[token] = Session(slug, is_admin, self._clock() + SESSION_TTL_SECONDS)
        return token

    def find(self, token: str | None, slug: str) -> Session | None:
        """Return the token's session if it is still valid for this app, otherwise None."""
        if not token:
            return None
        session = self._sessions.get(token)
        if session is None or session.slug != slug:
            return None
        if session.expires <= self._clock():
            del self._sessions[token]
            return None
        return session

    def forget_app(self, slug: str) -> None:
        """End every session for an app, when the app is removed or its settings change."""
        for token in [token for token, session in self._sessions.items() if session.slug == slug]:
            del self._sessions[token]

    def __len__(self) -> int:
        """How many tokens are held (the tests check old ones are cleaned up)."""
        return len(self._sessions)

    def _forget_expired(self) -> None:
        """Drop tokens that ran out, so an often-opened panel doesn't pile them up."""
        now = self._clock()
        for token in [token for token, session in self._sessions.items() if session.expires <= now]:
            del self._sessions[token]
