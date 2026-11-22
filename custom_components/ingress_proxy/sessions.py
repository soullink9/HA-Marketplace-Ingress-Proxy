"""Panel sessions: short-lived random tokens that let an app's iframe through the proxy.

The panel asks for one over Home Assistant's websocket (websocket.py), which proves who the user is. The token
then travels as a cookie on every request the iframe makes, because an iframe can't send Home Assistant's own
login. A token only opens the app it was made for, and only for as long as the panel keeps renewing it.

A token remembers which Home Assistant user it was given to, and the proxy checks that user again on every request
(proxy.py): a user who is deleted, deactivated, or no longer an administrator for an admins-only app is refused at
once, without waiting for the token to run out.

Tokens live in memory only, so they all end when Home Assistant restarts; the panel asks for a new one when its
websocket reconnects.
"""

from collections.abc import Callable
from dataclasses import dataclass
import secrets
import time

from .const import SESSION_TTL_SECONDS


@dataclass(frozen=True)
class Session:
    """Who a token was given to, and until when it works."""

    slug: str  # the one app this token opens
    user_id: str  # the Home Assistant user it was given to
    expires: float  # on the monotonic clock


class SessionStore:
    """Hands out tokens and checks them."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        """Start empty. `clock` is only swapped by the tests."""
        self._clock = clock
        self._sessions: dict[str, Session] = {}

    def create(self, slug: str, user_id: str) -> str:
        """Make a new token for one app and one Home Assistant user, and return it."""
        self._forget_expired()
        token = secrets.token_urlsafe(32)
        self._sessions[token] = Session(slug, user_id, self._clock() + SESSION_TTL_SECONDS)
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

    def forget_user(self, user_id: str) -> None:
        """End every session of a Home Assistant user, when the user is removed."""
        for token in [token for token, session in self._sessions.items() if session.user_id == user_id]:
            del self._sessions[token]

    def __len__(self) -> int:
        """How many tokens are held (the tests check old ones are cleaned up)."""
        return len(self._sessions)

    def _forget_expired(self) -> None:
        """Drop tokens that ran out, so an often-opened panel doesn't pile them up."""
        now = self._clock()
        for token in [token for token, session in self._sessions.items() if session.expires <= now]:
            del self._sessions[token]
