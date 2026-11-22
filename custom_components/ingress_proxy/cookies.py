"""The apps' own cookies: kept in Home Assistant for "persist login" apps, or passed to the browser for the rest.

With persist login on, the app's Set-Cookie headers never reach the browser. Their values are saved in
.storage/ingress_proxy.user_cookies, by app and by Home Assistant user, and added to that user's requests instead.
So logging in to the app once logs that user in on every device, and survives the browser clearing its cookies, but
each Home Assistant user has a login of their own: one user's login (an administrator's, say) is never sent on
another user's requests, and logging out logs out only that user.

With it off, the cookies go to the browser, rewritten so they belong to the app's own path on Home Assistant's
address: Domain is dropped (it would name the app's internal host, and the browser would refuse the cookie) and
Path is kept under the app's prefix (so the app's cookies can't collide with Home Assistant's or another app's).
Cookies the browser would treat specially are dropped: the proxy's own session cookies, and names starting with
__Host- or __Secure-, which promise the browser a cookie for the whole site.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any, Protocol

from .const import COOKIE_SAVE_DELAY_SECONDS, SESSION_COOKIE_PREFIX

# Cookie name prefixes the browser gives a meaning to (RFC 6265bis, section 4.1.3), matched in any case as browsers
# do. A __Host- cookie must have Path=/, which an app's cookie never gets here.
_SPECIAL_PREFIXES = ("__host-", "__secure-")


@dataclass
class SetCookie:
    """One Set-Cookie header, split into the cookie and its attributes.

    Parsed by hand rather than with http.cookies.SimpleCookie, which reads an attribute it doesn't know (such as
    Chrome's Priority=High) as a second cookie.
    """

    name: str
    value: str
    attributes: list[tuple[str, str]]  # in the order sent, names as sent; flags like HttpOnly have the value ""

    @classmethod
    def parse(cls, header: str) -> SetCookie | None:
        """Read a Set-Cookie header, or return None if it isn't one (no name, or no "=")."""
        first, *rest = header.split(";")
        name, equals, value = first.partition("=")
        name = name.strip()
        if not equals or not name:
            return None
        attributes = []
        for part in rest:
            key, _, attribute_value = part.partition("=")
            if key.strip():
                attributes.append((key.strip(), attribute_value.strip()))
        return cls(name, value.strip(), attributes)

    def get(self, attribute: str) -> str | None:
        """An attribute's value by name (any case), or None if it isn't set."""
        for key, value in self.attributes:
            if key.lower() == attribute.lower():
                return value
        return None

    def is_deletion(self, now: datetime) -> bool:
        """Whether the app is deleting the cookie: an empty value, Max-Age of 0 or less, or an Expires in the past."""
        if self.value in ("", '""'):
            return True
        max_age = self.get("Max-Age")
        if max_age is not None:
            # Max-Age wins over Expires when both are sent.
            try:
                return int(max_age) <= 0
            except ValueError:
                pass
        expires = self.get("Expires")
        if expires is not None:
            try:
                expiry = parsedate_to_datetime(expires)
            except TypeError, ValueError:
                return False
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=UTC)
            return expiry <= now
        return False

    def to_header(self) -> str:
        """Write it back out as a Set-Cookie header value."""
        parts = [f"{self.name}={self.value}"]
        for key, value in self.attributes:
            parts.append(f"{key}={value}" if value else key)
        return "; ".join(parts)


def is_proxy_cookie(name: str) -> bool:
    """Whether a cookie name is one of the proxy's own session cookies, which no app may set or see."""
    return name.startswith(SESSION_COOKIE_PREFIX)


def rewrite_for_browser(header: str, prefix: str, keep_prefix: bool) -> str | None:
    """Make an app's Set-Cookie header fit the app's path on Home Assistant's address.

    Returns None for a header to drop: one that can't be read, one that would overwrite one of the proxy's own
    session cookies, or one with a __Host- or __Secure- name. Its Path always ends up inside the app's prefix.
    """
    cookie = SetCookie.parse(header)
    if cookie is None or is_proxy_cookie(cookie.name) or cookie.name.lower().startswith(_SPECIAL_PREFIXES):
        return None
    attributes = []
    for key, value in cookie.attributes:
        if key.lower() == "domain":
            continue
        if key.lower() == "path":
            value = _path_in_app(value, prefix, keep_prefix)
        attributes.append((key, value))
    cookie.attributes = attributes
    return cookie.to_header()


def _path_in_app(path: str, prefix: str, keep_prefix: bool) -> str:
    """A cookie's Path, kept inside the app's prefix.

    The app thinks it lives at "/", so its paths are moved under the prefix. With keep prefix on, the app already
    writes paths with the prefix; one outside it (Path=/, say) becomes the prefix. A path that isn't a path at all
    (no leading "/") becomes the prefix too, as would a path that climbs out with "..".
    """
    if not path.startswith("/") or ".." in path.split("/"):
        return prefix
    if not keep_prefix:
        return prefix if path == "/" else prefix + path
    if path == prefix or path.startswith(prefix + "/"):
        return path
    return prefix


def values_named(cookie_headers: list[str], name: str) -> list[str]:
    """Every value the browser sent for one cookie name, in the order sent.

    A browser sends one value per path a cookie was set for, longest path first. Reading the header into a mapping
    (as aiohttp's request.cookies does) keeps only the last of them, which can be a stale one from a wider path.
    """
    values = []
    for header in cookie_headers:
        for pair in header.split(";"):
            pair_name, equals, value = pair.partition("=")
            if equals and pair_name.strip() == name:
                values.append(value.strip())
    return values


class _Store(Protocol):
    """The part of homeassistant.helpers.storage.Store this module uses."""

    async def async_load(self) -> Any:
        """Return what was saved, or None."""

    def async_delay_save(self, data_func: Any, delay: float = 0) -> None:
        """Save what data_func() returns after `delay` seconds."""


class UpstreamCookieJar:
    """The persist-login apps' cookies, by app slug and Home Assistant user, saved to Home Assistant's storage."""

    def __init__(self, store: _Store) -> None:
        """Keep the store; call async_load before use."""
        self._store = store
        self._cookies: dict[str, dict[str, dict[str, str]]] = {}  # {slug: {user id: {cookie name: value}}}

    async def async_load(self) -> None:
        """Read the cookies saved before the last restart."""
        self._cookies = await self._store.async_load() or {}

    def cookies_for(self, slug: str, user_id: str) -> dict[str, str]:
        """The cookies to send to an app for one user (a copy, so callers can't change the jar by accident)."""
        return dict(self._cookies.get(slug, {}).get(user_id, {}))

    def update(self, slug: str, user_id: str, set_cookie_headers: list[str], now: datetime | None = None) -> None:
        """Apply an app's Set-Cookie headers to one user's cookies: keep new values, forget deleted ones, then save."""
        if not set_cookie_headers:
            return
        now = now or datetime.now(UTC)
        app_cookies = self._cookies.setdefault(slug, {})
        user_cookies = app_cookies.setdefault(user_id, {})
        for header in set_cookie_headers:
            cookie = SetCookie.parse(header)
            if cookie is None or is_proxy_cookie(cookie.name):
                continue
            if cookie.is_deletion(now):
                user_cookies.pop(cookie.name, None)
            else:
                user_cookies[cookie.name] = cookie.value
        if not user_cookies:
            del app_cookies[user_id]
        if not app_cookies:
            del self._cookies[slug]
        self._save()

    def forget_app(self, slug: str) -> None:
        """Delete an app's cookies, for every user (when the app is removed from Home Assistant)."""
        if self._cookies.pop(slug, None) is not None:
            self._save()

    def forget_user(self, user_id: str) -> None:
        """Delete a user's cookies, for every app (when the user is removed from Home Assistant)."""
        changed = False
        for slug in list(self._cookies):
            if self._cookies[slug].pop(user_id, None) is not None:
                changed = True
                if not self._cookies[slug]:
                    del self._cookies[slug]
        if changed:
            self._save()

    def _save(self) -> None:
        """Save a few seconds from now, so the several cookies a login sets are written once."""
        self._store.async_delay_save(lambda: self._cookies, COOKIE_SAVE_DELAY_SECONDS)
