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
    logins.py        Home Assistant's own login to an app (HTTP Basic, or an ESPHome Device Builder token)
    client.py        the HTTP client used to reach the apps
    frontend/panel.js  the sidebar panel: a toolbar and the app's iframe
"""

from pathlib import Path

from homeassistant.auth import EVENT_USER_REMOVED
from homeassistant.components import frontend, panel_custom
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.storage import Store
from homeassistant.helpers.typing import ConfigType
from homeassistant.loader import async_get_integration

from . import websocket
from .client import async_reaches_metadata_service
from .const import (
    CONF_SLUG,
    COOKIE_STORE_KEY,
    COOKIE_STORE_VERSION,
    DOMAIN,
    PANEL_SCRIPT,
    PANEL_URL_PREFIX,
    SHARED_COOKIE_STORE_KEY,
    STATIC_URL,
)
from .cookies import UpstreamCookieJar
from .models import DATA_KEY, IngressProxyData, ProxiedApp
from .proxy import IngressProxyView

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
type IngressProxyConfigEntry = ConfigEntry[ProxiedApp]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Start what all the apps share: the proxy, the panel script, the session command and the saved cookies."""
    # Before 1.5.0 an app's kept cookies were shared by every user who could open it. Nobody can tell whose login
    # they hold, so they're deleted rather than handed to anyone: each user logs in to the app once more.
    await Store(hass, COOKIE_STORE_VERSION, SHARED_COOKIE_STORE_KEY).async_remove()
    cookie_jar = UpstreamCookieJar(Store(hass, COOKIE_STORE_VERSION, COOKIE_STORE_KEY))
    await cookie_jar.async_load()
    data = IngressProxyData(cookie_jar=cookie_jar)
    hass.data[DATA_KEY] = data

    @callback
    def forget_user(event: Event) -> None:
        """A Home Assistant user was deleted: end their sessions and delete the app logins kept for them."""
        data.sessions.forget_user(event.data["user_id"])
        data.cookie_jar.forget_user(event.data["user_id"])

    hass.bus.async_listen(EVENT_USER_REMOVED, forget_user)

    hass.http.register_view(IngressProxyView(hass, data))
    await hass.http.async_register_static_paths(
        [StaticPathConfig(STATIC_URL, str(Path(__file__).parent / "frontend"), cache_headers=False)]
    )
    websocket.async_register(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: IngressProxyConfigEntry) -> bool:
    """Add one app's sidebar panel and let the proxy serve it."""
    app = ProxiedApp.from_entry_data(entry.data)
    if await async_reaches_metadata_service(hass, app.url):
        # The setup form refuses these addresses; this catches a name whose address has changed since.
        raise ConfigEntryError(f"{app.url} leads to a cloud metadata service, which Ingress Proxy doesn't proxy")
    entry.runtime_data = app
    data = hass.data[DATA_KEY]
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
    data = hass.data[DATA_KEY]
    frontend.async_remove_panel(hass, PANEL_URL_PREFIX + app.slug, warn_if_unknown=False)
    data.apps.pop(app.slug, None)
    data.sessions.forget_app(app.slug)
    data.logins.forget(app.slug)  # the username or password may be what changed
    return True


async def async_remove_entry(hass: HomeAssistant, entry: IngressProxyConfigEntry) -> None:
    """Delete the cookies kept for an app that is removed, so its login doesn't linger in Home Assistant."""
    data = hass.data.get(DATA_KEY)
    if data is None:
        # Removed before the integration started (it was disabled, say): open the saved cookies directly.
        cookie_jar = UpstreamCookieJar(Store(hass, COOKIE_STORE_VERSION, COOKIE_STORE_KEY))
        await cookie_jar.async_load()
    else:
        cookie_jar = data.cookie_jar
    cookie_jar.forget_app(entry.data[CONF_SLUG])
