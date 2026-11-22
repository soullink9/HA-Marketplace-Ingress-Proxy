"""The one websocket command: the panel asks for a session to open its app with."""

from typing import Any

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback
import probatio

from .const import DOMAIN, RESERVED_LOCAL_STORAGE_KEYS, SESSION_TTL_SECONDS, session_cookie_name
from .models import DATA_KEY


@callback
def async_register(hass: HomeAssistant) -> None:
    """Register the command with Home Assistant's websocket API."""
    websocket_api.async_register_command(hass, ws_session)


@websocket_api.websocket_command({probatio.Required("type"): f"{DOMAIN}/session", probatio.Required("slug"): str})
@callback
def ws_session(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    """Give the panel a session token for one app, and tell it how to store it.

    Home Assistant has already checked who is asking: this runs on their logged-in websocket. The answer also
    carries the app's browser storage entries, so the panel can write them before the app loads.
    """
    data = hass.data[DATA_KEY]
    app = data.apps.get(msg["slug"])
    if app is None:
        connection.send_error(msg["id"], websocket_api.ERR_NOT_FOUND, "There is no app with this slug")
        return
    user = connection.user
    if app.require_admin and not user.is_admin:
        connection.send_error(msg["id"], websocket_api.ERR_UNAUTHORIZED, "Only administrators can open this app")
        return
    connection.send_result(
        msg["id"],
        {
            "session": data.sessions.create(app.slug, user.id),
            "cookie": session_cookie_name(app.slug),
            "path": app.prefix,
            "max_age": SESSION_TTL_SECONDS,
            # The setup form refuses Home Assistant's own keys; an entry saved before it did is filtered here.
            "local_storage": {
                key: value for key, value in app.local_storage.items() if key not in RESERVED_LOCAL_STORAGE_KEYS
            },
        },
    )
