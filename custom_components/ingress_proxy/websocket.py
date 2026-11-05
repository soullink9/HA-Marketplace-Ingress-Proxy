"""The one websocket command: the panel asks for a session to open its app with."""

from __future__ import annotations

from typing import Any

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback
import voluptuous as vol

from .const import DOMAIN, SESSION_COOKIE, SESSION_TTL_SECONDS
from .models import IngressProxyData


@callback
def async_register(hass: HomeAssistant) -> None:
    """Register the command with Home Assistant's websocket API."""
    websocket_api.async_register_command(hass, ws_session)


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/session", vol.Required("slug"): str})
@callback
def ws_session(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    """Give the panel a session token for one app, and tell it how to store it.

    Home Assistant has already checked who is asking: this runs on their logged-in websocket. The answer also
    carries the app's browser storage entries, so the panel can write them before the app loads.
    """
    data: IngressProxyData = hass.data[DOMAIN]
    app = data.apps.get(msg["slug"])
    if app is None:
        connection.send_error(msg["id"], websocket_api.ERR_NOT_FOUND, "There is no app with this slug")
        return
    is_admin = connection.user.is_admin
    if app.require_admin and not is_admin:
        connection.send_error(msg["id"], websocket_api.ERR_UNAUTHORIZED, "Only administrators can open this app")
        return
    connection.send_result(
        msg["id"],
        {
            "session": data.sessions.create(app.slug, is_admin),
            "cookie": SESSION_COOKIE,
            "path": app.prefix,
            "max_age": SESSION_TTL_SECONDS,
            "local_storage": dict(app.local_storage),
        },
    )
