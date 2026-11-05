"""The setup form: add an app, and change its settings later with Reconfigure. One config entry per app."""

from __future__ import annotations

from collections.abc import Mapping
import re
from typing import Any
from urllib.parse import urlsplit

import aiohttp
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_URL
from homeassistant.data_entry_flow import section
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.selector import (
    IconSelector,
    ObjectSelector,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
import voluptuous as vol

from .client import async_get_client
from .const import (
    CHECK_TIMEOUT_SECONDS,
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
    DOMAIN,
)

# The less common settings sit in a collapsed "Advanced" section of the form.
ADVANCED = "advanced"
ADVANCED_KEYS = (CONF_KEEP_PREFIX, CONF_VERIFY_SSL, CONF_HEADERS, CONF_LOCAL_STORAGE)

# A header name is a "token" (RFC 9110, section 5.6.2).
_HEADER_NAME = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")


def _schema(include_slug: bool) -> vol.Schema:
    """The form. The slug is only asked for when adding an app: it is part of the panel's address."""
    fields: dict[Any, Any] = {}
    if include_slug:
        fields[vol.Required(CONF_SLUG)] = str
    fields[vol.Required(CONF_TITLE)] = str
    fields[vol.Required(CONF_URL)] = TextSelector(TextSelectorConfig(type=TextSelectorType.URL))
    fields[vol.Required(CONF_ICON, default=DEFAULT_ICON)] = IconSelector()
    fields[vol.Required(CONF_REQUIRE_ADMIN, default=True)] = bool
    fields[vol.Required(CONF_PERSIST_LOGIN, default=True)] = bool
    fields[vol.Required(ADVANCED)] = section(
        vol.Schema(
            {
                vol.Required(CONF_KEEP_PREFIX, default=False): bool,
                vol.Required(CONF_VERIFY_SSL, default=True): bool,
                vol.Optional(CONF_HEADERS): ObjectSelector(),
                vol.Optional(CONF_LOCAL_STORAGE): ObjectSelector(),
            }
        ),
        {"collapsed": True},
    )
    return vol.Schema(fields)


def _form_values(data: Mapping[str, Any]) -> dict[str, Any]:
    """Saved (flat) settings in the form's shape, with the advanced ones inside their section."""
    values = {key: value for key, value in data.items() if key not in ADVANCED_KEYS}
    values[ADVANCED] = {key: data[key] for key in ADVANCED_KEYS if key in data}
    return values


def clean_url(raw: str) -> str | None:
    """The app's address without spaces or a trailing "/", or None if it isn't a plain http(s) address."""
    url = raw.strip().rstrip("/")
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None
    if parts.query or parts.fragment or parts.username or parts.password:
        return None
    return url


def clean_slug(raw: str) -> str | None:
    """The slug if it is lowercase letters, digits and underscores (Home Assistant's slug rule), otherwise None."""
    slug = raw.strip()
    try:
        return cv.slug(slug)
    except vol.Invalid:
        return None


def clean_headers(raw: Any) -> dict[str, str] | None:
    """Headers typed as a YAML mapping of name: value, or None if they can't be sent as headers.

    Numbers and true/false are turned into text. Lists, nested mappings and line breaks are refused; a line break
    would let a value smuggle in extra headers.
    """
    if raw in (None, ""):
        return {}
    if not isinstance(raw, Mapping):
        return None
    headers = {}
    for name, value in raw.items():
        if not isinstance(name, str) or not _HEADER_NAME.match(name):
            return None
        if isinstance(value, (Mapping, list)) or value is None:
            return None
        text = str(value)
        if "\r" in text or "\n" in text:
            return None
        headers[name] = text
    return headers


def clean_local_storage(raw: Any) -> dict[str, str] | None:
    """Browser storage entries typed as a YAML mapping of key: value, or None if one isn't plain text."""
    if raw in (None, ""):
        return {}
    if not isinstance(raw, Mapping):
        return None
    entries = {}
    for key, value in raw.items():
        if not isinstance(key, str) or isinstance(value, (Mapping, list)) or value is None:
            return None
        entries[key] = str(value)
    return entries


class IngressProxyConfigFlow(ConfigFlow, domain=DOMAIN):
    """Ask for the app's details, check Home Assistant can reach it, then save it."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Add an app (Settings > Devices & services > Add integration > Ingress Proxy)."""
        return await self._async_form("user", user_input)

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Change an app's settings. Everything but the slug can change."""
        return await self._async_form("reconfigure", user_input, self._get_reconfigure_entry())

    async def _async_form(
        self, step_id: str, user_input: dict[str, Any] | None, entry: ConfigEntry | None = None
    ) -> ConfigFlowResult:
        """Show the form; when it's sent, tidy and check it, then create or update the entry."""
        errors: dict[str, str] = {}
        if user_input is not None:
            data, errors = self._tidy(user_input, entry)
            if not errors:
                errors = await self._async_check_reachable(data)
            if not errors:
                if entry is not None:
                    return self.async_update_reload_and_abort(entry, title=data[CONF_TITLE], data=data)
                await self.async_set_unique_id(data[CONF_SLUG])
                return self.async_create_entry(title=data[CONF_TITLE], data=data)

        # Pre-fill with what was just typed, or with the saved settings when changing an app.
        if user_input is not None:
            suggested = user_input
        elif entry is not None:
            suggested = _form_values(entry.data)
        else:
            suggested = None
        return self.async_show_form(
            step_id=step_id,
            data_schema=self.add_suggested_values_to_schema(_schema(include_slug=entry is None), suggested),
            errors=errors,
        )

    def _tidy(self, user_input: dict[str, Any], entry: ConfigEntry | None) -> tuple[dict[str, Any], dict[str, str]]:
        """Turn what was typed into the entry's flat data, and say what's wrong with it, field by field."""
        advanced = user_input.get(ADVANCED, {})
        errors: dict[str, str] = {}
        data: dict[str, Any] = {
            CONF_TITLE: user_input[CONF_TITLE].strip(),
            CONF_ICON: user_input.get(CONF_ICON) or DEFAULT_ICON,
            CONF_REQUIRE_ADMIN: user_input[CONF_REQUIRE_ADMIN],
            CONF_PERSIST_LOGIN: user_input[CONF_PERSIST_LOGIN],
            CONF_KEEP_PREFIX: advanced.get(CONF_KEEP_PREFIX, False),
            CONF_VERIFY_SSL: advanced.get(CONF_VERIFY_SSL, True),
        }

        if entry is None:
            slug = clean_slug(user_input[CONF_SLUG])
            if slug is None:
                errors[CONF_SLUG] = "invalid_slug"
            elif slug in self._async_current_ids(include_ignore=False):
                errors[CONF_SLUG] = "slug_taken"
            data[CONF_SLUG] = slug
        else:
            data[CONF_SLUG] = entry.data[CONF_SLUG]

        if not data[CONF_TITLE]:
            errors[CONF_TITLE] = "invalid_title"

        url = clean_url(user_input[CONF_URL])
        if url is None:
            errors[CONF_URL] = "invalid_url"
        data[CONF_URL] = url

        # Errors inside a section are shown at the top of the form, so they get their own base messages.
        headers = clean_headers(advanced.get(CONF_HEADERS))
        if headers is None:
            errors["base"] = "invalid_headers"
        data[CONF_HEADERS] = headers or {}
        local_storage = clean_local_storage(advanced.get(CONF_LOCAL_STORAGE))
        if local_storage is None:
            errors["base"] = "invalid_local_storage"
        data[CONF_LOCAL_STORAGE] = local_storage or {}
        return data, errors

    async def _async_check_reachable(self, data: dict[str, Any]) -> dict[str, str]:
        """Ask the app for its front page. Any reply at all, even an error page, shows the address works."""
        client = async_get_client(self.hass, data[CONF_VERIFY_SSL])
        try:
            async with client.get(
                data[CONF_URL] + "/",
                headers=data[CONF_HEADERS],
                allow_redirects=False,
                timeout=aiohttp.ClientTimeout(total=CHECK_TIMEOUT_SECONDS),
            ):
                pass
        except aiohttp.ClientConnectorCertificateError:
            return {"base": "certificate"}
        except (aiohttp.ClientError, TimeoutError):
            return {"base": "cannot_connect"}
        return {}
