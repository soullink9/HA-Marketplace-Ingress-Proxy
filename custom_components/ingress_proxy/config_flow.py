"""The setup form: add an app, and change its settings later with Reconfigure. One config entry per app."""

from __future__ import annotations

from collections.abc import Mapping
import re
from typing import Any
from urllib.parse import urlsplit

import aiohttp
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_PASSWORD, CONF_URL, CONF_USERNAME
from homeassistant.data_entry_flow import section
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.selector import (
    IconSelector,
    ObjectSelector,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
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
    CONF_LOGIN_METHOD,
    CONF_PERSIST_LOGIN,
    CONF_REQUIRE_ADMIN,
    CONF_SLUG,
    CONF_TITLE,
    CONF_VERIFY_SSL,
    DEFAULT_ICON,
    DOMAIN,
    LOGIN_BASIC,
    LOGIN_ESPHOME,
    LOGIN_METHODS,
)
from .logins import LoginRefused, NotEsphome, async_esphome_login, basic_authorization
from .models import ProxiedApp
from .rewrite import with_authorization

# The less common settings sit in a collapsed "Advanced" section of the form.
ADVANCED = "advanced"
ADVANCED_KEYS = (CONF_KEEP_PREFIX, CONF_VERIFY_SSL, CONF_HEADERS, CONF_LOCAL_STORAGE)

# Home Assistant's own login to the app sits in a "Login" section, open when the app has one.
LOGIN = "login"
LOGIN_KEYS = (CONF_USERNAME, CONF_PASSWORD, CONF_LOGIN_METHOD)

# A header name is a "token" (RFC 9110, section 5.6.2).
_HEADER_NAME = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")


def _schema(include_slug: bool, has_login: bool = False) -> vol.Schema:
    """The form. The slug is only asked for when adding an app: it is part of the panel's address.

    The Login section starts open when the app already has a username, so it's clear the proxy logs in.
    """
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
    fields[vol.Required(LOGIN)] = section(
        vol.Schema(
            {
                vol.Optional(CONF_USERNAME): TextSelector(TextSelectorConfig(autocomplete="username")),
                vol.Optional(CONF_PASSWORD): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.PASSWORD, autocomplete="new-password")
                ),
                vol.Required(CONF_LOGIN_METHOD, default=LOGIN_BASIC): SelectSelector(
                    SelectSelectorConfig(
                        options=list(LOGIN_METHODS),
                        translation_key=CONF_LOGIN_METHOD,
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
            }
        ),
        {"collapsed": not has_login},
    )
    return vol.Schema(fields)


def _form_values(data: Mapping[str, Any]) -> dict[str, Any]:
    """Saved (flat) settings in the form's shape, with the advanced and login ones inside their sections.

    The saved password is left out: the form would send it to the browser. Left blank, it stays as it is (_tidy).
    """
    values = {key: value for key, value in data.items() if key not in ADVANCED_KEYS and key not in LOGIN_KEYS}
    values[ADVANCED] = {key: data[key] for key in ADVANCED_KEYS if key in data}
    values[LOGIN] = {key: data[key] for key in (CONF_USERNAME, CONF_LOGIN_METHOD) if key in data}
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


def _tidy_login(login: Mapping[str, Any], entry: ConfigEntry | None) -> tuple[str, str, str, str | None]:
    """The login section as (username, password, login method, error or None).

    A blank password keeps the saved one, since the form never shows it. A blank username means no login, and drops
    the password with it.
    """
    username = (login.get(CONF_USERNAME) or "").strip()
    password = login.get(CONF_PASSWORD) or ""
    login_method = login.get(CONF_LOGIN_METHOD) or LOGIN_BASIC
    if login_method not in LOGIN_METHODS:
        login_method = LOGIN_BASIC
    if not username:
        return "", "", login_method, None
    if not password and entry is not None:
        password = entry.data.get(CONF_PASSWORD) or ""
    if ":" in username:
        # HTTP Basic joins the two with a colon, so a colon in the username would split it in the wrong place.
        return username, password, login_method, "invalid_username"
    if not password:
        return username, password, login_method, "missing_password"
    return username, password, login_method, None


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
        has_login = bool(entry is not None and entry.data.get(CONF_USERNAME))
        return self.async_show_form(
            step_id=step_id,
            data_schema=self.add_suggested_values_to_schema(
                _schema(include_slug=entry is None, has_login=has_login), suggested
            ),
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

        username, password, login_method, login_error = _tidy_login(user_input.get(LOGIN, {}), entry)
        if login_error is not None:
            errors["base"] = login_error
        data[CONF_USERNAME] = username
        data[CONF_PASSWORD] = password
        data[CONF_LOGIN_METHOD] = login_method
        return data, errors

    async def _async_check_reachable(self, data: dict[str, Any]) -> dict[str, str]:
        """Ask the app for its front page, then check the login if there is one.

        Any reply at all, even an error page, shows the address works. With a username, a 401 to HTTP Basic means
        the app refused it, and an ESPHome Device Builder is logged in to for real.
        """
        client = async_get_client(self.hass, data[CONF_VERIFY_SSL])
        uses_basic = bool(data[CONF_USERNAME]) and data[CONF_LOGIN_METHOD] == LOGIN_BASIC
        uses_esphome = bool(data[CONF_USERNAME]) and data[CONF_LOGIN_METHOD] == LOGIN_ESPHOME
        authorization = basic_authorization(data[CONF_USERNAME], data[CONF_PASSWORD]) if uses_basic else None
        headers = with_authorization(list(data[CONF_HEADERS].items()), authorization)
        try:
            async with client.get(
                data[CONF_URL] + "/",
                headers=headers,
                allow_redirects=False,
                timeout=aiohttp.ClientTimeout(total=CHECK_TIMEOUT_SECONDS),
            ) as response:
                status = response.status
        except aiohttp.ClientConnectorCertificateError:
            return {"base": "certificate"}
        except (aiohttp.ClientError, TimeoutError):
            return {"base": "cannot_connect"}
        if uses_basic and status == 401:
            return {"base": "invalid_auth"}

        if uses_esphome:
            try:
                await async_esphome_login(client, ProxiedApp.from_entry_data(data))
            except LoginRefused:
                return {"base": "invalid_auth"}
            except NotEsphome:
                return {"base": "not_esphome"}
            except (aiohttp.ClientError, TimeoutError):
                return {"base": "cannot_connect"}
        return {}
