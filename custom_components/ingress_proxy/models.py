"""What the integration keeps in memory: the proxied apps, the panel sessions and the upstream cookies."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from homeassistant.const import CONF_URL

from .const import (
    CONF_HEADERS,
    CONF_ICON,
    CONF_KEEP_PREFIX,
    CONF_LOCAL_STORAGE,
    CONF_PERSIST_LOGIN,
    CONF_REQUIRE_ADMIN,
    CONF_SLUG,
    CONF_TITLE,
    CONF_VERIFY_SSL,
    DEFAULT_ICON,
    PROXY_BASE,
)
from .cookies import UpstreamCookieJar
from .sessions import SessionStore


@dataclass(frozen=True)
class ProxiedApp:
    """One app shown in a sidebar panel, built from its config entry."""

    slug: str  # the app's part of every URL: /proxy-<slug> and PROXY_BASE/<slug>/
    title: str  # the sidebar title
    url: str  # where Home Assistant reaches the app, without a trailing "/" (http://music-assistant:8095)
    icon: str = DEFAULT_ICON
    keep_prefix: bool = False  # send PROXY_BASE/<slug>/... to the app instead of stripping it
    persist_login: bool = True  # keep the app's cookies in Home Assistant instead of the browser
    require_admin: bool = True  # only Home Assistant administrators may open it
    verify_ssl: bool = True  # check the app's certificate when its URL is https
    headers: Mapping[str, str] = field(default_factory=dict)  # added to every request (an API token, say)
    local_storage: Mapping[str, str] = field(default_factory=dict)  # written to the browser's localStorage

    @classmethod
    def from_entry_data(cls, data: Mapping[str, Any]) -> ProxiedApp:
        """Build the app from a config entry's data, filling in defaults for anything an older entry lacks."""
        return cls(
            slug=data[CONF_SLUG],
            title=data[CONF_TITLE],
            url=data[CONF_URL].rstrip("/"),
            icon=data.get(CONF_ICON) or DEFAULT_ICON,
            keep_prefix=data.get(CONF_KEEP_PREFIX, False),
            persist_login=data.get(CONF_PERSIST_LOGIN, True),
            require_admin=data.get(CONF_REQUIRE_ADMIN, True),
            verify_ssl=data.get(CONF_VERIFY_SSL, True),
            headers=dict(data.get(CONF_HEADERS) or {}),
            local_storage=dict(data.get(CONF_LOCAL_STORAGE) or {}),
        )

    @property
    def prefix(self) -> str:
        """The path the app is served under on Home Assistant's address, without a trailing "/"."""
        return f"{PROXY_BASE}/{self.slug}"


@dataclass
class IngressProxyData:
    """Everything shared by all the apps, kept in hass.data[DOMAIN] from start-up until Home Assistant stops."""

    cookie_jar: UpstreamCookieJar
    sessions: SessionStore = field(default_factory=SessionStore)
    apps: dict[str, ProxiedApp] = field(default_factory=dict)  # by slug; one per loaded config entry
