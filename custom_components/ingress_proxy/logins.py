"""Home Assistant's own login to an app, for apps set up with a username and password.

The proxy logs in for whoever opens the panel, so the app never asks for its password in the browser. The username,
password and any token the app hands out only travel between Home Assistant and the app: they go in the Authorization
header of the requests the proxy makes (rewrite.with_authorization), never into a reply to the browser.

Two ways of logging in, chosen in the app's settings:
  - HTTP Basic: the username and password go with every request. Most apps that ask for a login in a browser pop-up
    take this, and so did ESPHome's dashboard before ESPHome Device Builder replaced it.
  - ESPHome Device Builder: it takes a username and password only on its websocket's "auth/login" command, and a
    browser's websocket can't carry one in a header. So the proxy logs in over a websocket of its own, keeps the
    token it gets back, and sends that as "Authorization: Bearer <token>" on every request and websocket. The
    Device Builder then treats the page as logged in and doesn't show its login screen.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from typing import TYPE_CHECKING, Any

import aiohttp

from .const import CHECK_TIMEOUT_SECONDS, LOGIN_ESPHOME
from .rewrite import websocket_url

if TYPE_CHECKING:
    from .models import ProxiedApp

_LOGGER = logging.getLogger(__name__)

# The ESPHome Device Builder's websocket, and the id the proxy gives its login command so it can find the reply.
ESPHOME_WEBSOCKET_PATH = "/ws"
ESPHOME_LOGIN_ID = "ingress_proxy_login"

# After a login fails (refused, or not a Device Builder), wait this long before trying again. Every request of a page
# load would otherwise try, and the Device Builder locks out an address after a few failed logins.
RETRY_REFUSED_LOGIN_SECONDS = 60


class LoginRefused(Exception):
    """The app answered, but refused the username and password (or is rate-limiting failed logins)."""


class NotEsphome(Exception):
    """Whatever answered isn't an ESPHome Device Builder: no websocket at /ws, or one that speaks something else."""


def basic_authorization(username: str, password: str) -> str:
    """The Authorization header value for HTTP Basic (RFC 7617), in UTF-8."""
    pair = f"{username}:{password}".encode()
    return "Basic " + base64.b64encode(pair).decode("ascii")


def esphome_wants_login(message: str) -> bool:
    """Whether the Device Builder's first websocket message says this connection still has to log in.

    The Device Builder opens every websocket with a server-info message whose requires_auth is false once the
    connection is logged in (the Bearer token was accepted), and true when the page will have to show its login.
    """
    try:
        info = json.loads(message)
    except ValueError:
        return False
    return isinstance(info, dict) and info.get("requires_auth") is True


async def async_esphome_login(client: aiohttp.ClientSession, app: ProxiedApp) -> str | None:
    """Log in to an ESPHome Device Builder over its websocket and return the session token it gives.

    Returns None when the Device Builder has no password set, so there's nothing to log in to. Raises LoginRefused
    for a wrong username or password, NotEsphome when the app doesn't talk like a Device Builder, and aiohttp's
    errors or TimeoutError when it can't be reached.
    """
    url = websocket_url(app.url + ESPHOME_WEBSOCKET_PATH)
    try:
        async with asyncio.timeout(CHECK_TIMEOUT_SECONDS):
            async with client.ws_connect(url) as ws:
                info = await _receive_json(ws)
                if info.get("requires_auth") is not True:
                    if "requires_auth" not in info:
                        raise NotEsphome("the websocket didn't start with the Device Builder's server info")
                    return None
                await ws.send_str(
                    json.dumps(
                        {
                            "command": "auth/login",
                            "message_id": ESPHOME_LOGIN_ID,
                            "args": {"username": app.username, "password": app.password},
                        }
                    )
                )
                while True:
                    reply = await _receive_json(ws)
                    if reply.get("message_id") != ESPHOME_LOGIN_ID:
                        continue  # an event meant for someone else; the Device Builder doesn't send any before login
                    result = reply.get("result")
                    if isinstance(result, dict) and isinstance(result.get("token"), str):
                        return result["token"]
                    # The details say why ("Invalid credentials", "Too many failed attempts"), never the password.
                    raise LoginRefused(str(reply.get("details") or reply.get("error_code") or "refused"))
    except aiohttp.WSServerHandshakeError as err:
        # Something answered at the address, but with no websocket at /ws: not a Device Builder.
        raise NotEsphome(f"no websocket at {ESPHOME_WEBSOCKET_PATH} ({err.status})") from err


async def _receive_json(ws: aiohttp.ClientWebSocketResponse) -> dict[str, Any]:
    """The next websocket message, as a JSON object. Anything else means this isn't a Device Builder."""
    message = await ws.receive()
    if message.type is not aiohttp.WSMsgType.TEXT:
        raise NotEsphome(f"expected a text message, got {message.type}")
    try:
        data = json.loads(message.data)
    except ValueError as err:
        raise NotEsphome("expected JSON") from err
    if not isinstance(data, dict):
        raise NotEsphome("expected a JSON object")
    return data


class UpstreamLogins:
    """The Authorization header to send each app, with the ESPHome Device Builder tokens kept in memory.

    Tokens aren't saved: after a restart the proxy simply logs in again, on the first request. They last 30 days
    from their last use on the Device Builder's side, so in practice a token is only lost when someone presses Log out
    in the Device Builder; forget() then makes the next request log in again.
    """

    def __init__(self) -> None:
        """Start with no tokens."""
        self._tokens: dict[str, str | None] = {}  # by slug; None: the Device Builder has no password set
        self._locks: dict[str, asyncio.Lock] = {}
        self._refused: dict[str, tuple[float, Exception]] = {}  # by slug: when the login last failed, and why

    async def async_authorization(self, client: aiohttp.ClientSession, app: ProxiedApp) -> str | None:
        """The Authorization header value for a request to the app, or None if the proxy doesn't log in to it.

        For an ESPHome Device Builder this logs in first if there's no token yet, so it can raise what
        async_esphome_login raises.
        """
        if not app.username:
            return None
        if app.login_method != LOGIN_ESPHOME:
            return basic_authorization(app.username, app.password)

        # A page load starts many requests at once; the lock makes them wait for one login instead of each making one.
        lock = self._locks.setdefault(app.slug, asyncio.Lock())
        async with lock:
            if app.slug not in self._tokens:
                refused = self._refused.get(app.slug)
                if refused is not None and time.monotonic() - refused[0] < RETRY_REFUSED_LOGIN_SECONDS:
                    raise refused[1]
                try:
                    self._tokens[app.slug] = await async_esphome_login(client, app)
                except (LoginRefused, NotEsphome) as err:
                    _LOGGER.warning("Home Assistant couldn't log in to %s: %s", app.title, err)
                    self._refused[app.slug] = (time.monotonic(), err)
                    raise
                self._refused.pop(app.slug, None)
                _LOGGER.debug("Logged in to %s", app.slug)
        token = self._tokens[app.slug]
        return None if token is None else f"Bearer {token}"

    def forget(self, slug: str) -> None:
        """Drop the app's token, so the next request logs in again: the app refused it, or its settings changed.

        A recent refusal is forgotten too, so new settings are tried at once.
        """
        self._tokens.pop(slug, None)
        self._refused.pop(slug, None)
