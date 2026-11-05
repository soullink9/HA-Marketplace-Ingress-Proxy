"""Ingress Proxy: show the web pages of apps on your network as Home Assistant sidebar panels.

Each app is one config entry. Its panel loads the app in an iframe from Home Assistant's own address, through a
reverse proxy that only lets in people logged in to Home Assistant, so the app needs no port of its own on your
network and no login of its own in the browser.

Set-up overview (see docs/Development.md for the full tour):
    config_flow.py   the form that adds an app, and changes it later (Reconfigure)
    models.py        ProxiedApp (one app's settings) and IngressProxyData (shared by all apps)
    websocket.py     the panel asks for a session here
    sessions.py      the session tokens that let a panel's iframe through the proxy
    proxy.py         the reverse proxy, HTTP and websockets
    rewrite.py       what is passed on and how it is rewritten, both ways
    cookies.py       the apps' own cookies, kept in Home Assistant or passed to the browser
    client.py        the HTTP client used to reach the apps
    frontend/panel.js  the sidebar panel: a toolbar and the app's iframe
"""

from __future__ import annotations

from pathlib import Path

from homeassistant.components import frontend, panel_custom
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.storage import Store
from homeassistant.helpers.typing import ConfigType
from homeassistant.loader import async_get_integration

from . import websocket
from .const import (
    CONF_SLUG,
    COOKIE_STORE_KEY,
    COOKIE_STORE_VERSION,
    DOMAIN,
    PANEL_SCRIPT,
    PANEL_URL_PREFIX,
    STATIC_URL,
)
from .cookies import UpstreamCookieJar
from .models import IngressProxyData, ProxiedApp
from .proxy import IngressProxyView

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
type IngressProxyConfigEntry = ConfigEntry[ProxiedApp]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Start what all the apps share: the proxy, the panel script, the session command and the saved cookies."""
    cookie_jar = UpstreamCookieJar(Store(hass, COOKIE_STORE_VERSION, COOKIE_STORE_KEY))
    await cookie_jar.async_load()
    data = IngressProxyData(cookie_jar=cookie_jar)
    hass.data[DOMAIN] = data

    hass.http.register_view(IngressProxyView(hass, data))
    await hass.http.async_register_static_paths(
        [StaticPathConfig(STATIC_URL, str(Path(__file__).parent / "frontend"), cache_headers=False)]
    )
    websocket.async_register(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: IngressProxyConfigEntry) -> bool:
    """Add one app's sidebar panel and let the proxy serve it."""
    app = ProxiedApp.from_entry_data(entry.data)
    entry.runtime_data = app
    data: IngressProxyData = hass.data[DOMAIN]
    data.apps[app.slug] = app

    # The version goes in the script's URL, so browsers fetch the new script after an update, and in the
    # element's name, so a browser that still has the old script loaded uses the new one instead.
    version = str((await async_get_integration(hass, DOMAIN)).version)
    await panel_custom.async_register_panel(
        hass,
        frontend_url_path=PANEL_URL_PREFIX + app.slug,
        webcomponent_name=f"ingress-proxy-panel-{version.replace('.', '-')}",
        module_url=f"{STATIC_URL}/{PANEL_SCRIPT}?v={version}",
        sidebar_title=app.title,
        sidebar_icon=app.icon,
        require_admin=app.require_admin,
        config={"slug": app.slug},
    )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: IngressProxyConfigEntry) -> bool:
    """Remove the app's panel and stop serving it. Open sessions end, so a changed setting applies at once."""
    app = entry.runtime_data
    data: IngressProxyData = hass.data[DOMAIN]
    frontend.async_remove_panel(hass, PANEL_URL_PREFIX + app.slug, warn_if_unknown=False)
    data.apps.pop(app.slug, None)
    data.sessions.forget_app(app.slug)
    return True


async def async_remove_entry(hass: HomeAssistant, entry: IngressProxyConfigEntry) -> None:
    """Delete the cookies kept for an app that is removed, so its login doesn't linger in Home Assistant."""
    data: IngressProxyData | None = hass.data.get(DOMAIN)
    if data is None:
        # Removed before the integration started (it was disabled, say): open the saved cookies directly.
        cookie_jar = UpstreamCookieJar(Store(hass, COOKIE_STORE_VERSION, COOKIE_STORE_KEY))
        await cookie_jar.async_load()
    else:
        cookie_jar = data.cookie_jar
    cookie_jar.forget_app(entry.data[CONF_SLUG])
