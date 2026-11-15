"""The reverse proxy itself: passes the panel iframe's requests and websockets to the app and back.

Every request is checked for a valid panel session (sessions.py) before anything reaches the app. The rules for
what is passed on and how it is rewritten are in rewrite.py; this module only moves the bytes.
"""

from __future__ import annotations

import asyncio
import logging
from urllib.parse import urlsplit

import aiohttp
from aiohttp import WSMsgType, web
from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant

from .client import async_get_client
from .const import LOGIN_ESPHOME, PROXY_BASE, SESSION_COOKIE, STREAM_CHUNK_BYTES
from .cookies import rewrite_for_browser, values_named
from .logins import ESPHOME_WEBSOCKET_PATH, LoginRefused, NotEsphome, esphome_wants_login
from .models import IngressProxyData, ProxiedApp
from .rewrite import (
    close_code_to_send,
    has_parent_segment,
    request_headers,
    response_headers,
    upstream_url,
    websocket_url,
    with_authorization,
)
from .sessions import Session

_LOGGER = logging.getLogger(__name__)

# What the app being down or failing looks like from here: refused, timed out, reset, or a bad reply.
UPSTREAM_ERRORS = (aiohttp.ClientError, TimeoutError)

NO_SESSION_TEXT = "Ingress Proxy: no valid session. Open this app from its panel in the Home Assistant sidebar."

# What logging in to the app can fail with, besides the app being down.
LOGIN_ERRORS = (LoginRefused, NotEsphome)


class IngressProxyView(HomeAssistantView):
    """Serves every app at PROXY_BASE/<slug>/, over HTTP and websockets."""

    url = PROXY_BASE + "/{slug}/{path:.*}"
    extra_urls = [PROXY_BASE + "/{slug}"]
    name = "api:ingress_proxy"
    # The iframe can't send Home Assistant's bearer token, so Home Assistant's own check would refuse every request.
    # The panel session cookie is checked instead, in _proxy, before anything else happens.
    requires_auth = False

    def __init__(self, hass: HomeAssistant, data: IngressProxyData) -> None:
        """Keep what every request needs."""
        self.hass = hass
        self.data = data

    async def _proxy(self, request: web.Request, slug: str, path: str = "") -> web.StreamResponse:
        """Check the session, then pass the request to the app.

        `path` is the route's decoded match and isn't used: the path is taken from the raw URL instead, still
        percent-encoded, so the app gets exactly what the browser sent.
        """
        app = self.data.apps.get(slug)
        if app is None:
            return web.Response(status=404)

        session = self._find_session(request, slug)
        if session is None or (app.require_admin and not session.is_admin):
            _LOGGER.debug("Refused %s %s: no valid panel session", request.method, request.path)
            return web.Response(status=401, text=NO_SESSION_TEXT)

        raw_path = request.rel_url.raw_path
        if raw_path == app.prefix:
            # Send the browser to the address with the trailing "/", so the app's relative links resolve under it.
            location = app.prefix + "/"
            if request.rel_url.raw_query_string:
                location += "?" + request.rel_url.raw_query_string
            return web.Response(status=307, headers={"Location": location})
        raw_tail = raw_path[len(app.prefix) :]
        if has_parent_segment(raw_tail):
            return web.Response(status=400)

        target = upstream_url(app, raw_tail, request.rel_url.raw_query_string)
        cookies = {name: value for name, value in request.cookies.items() if name != SESSION_COOKIE}
        if app.persist_login:
            cookies.update(self.data.cookie_jar.cookies_for(slug))
        headers = request_headers(
            request.headers.items(),
            app,
            cookies,
            forwarded_host=request.host,
            forwarded_proto=request.scheme,
            forwarded_for=request.remote,
        )

        client = async_get_client(self.hass, app.verify_ssl)
        try:
            authorization = await self.data.logins.async_authorization(client, app)
        except UPSTREAM_ERRORS as err:
            _LOGGER.debug("Logging in to %s failed: %r", app.slug, err)
            return web.Response(status=502, text=f"Ingress Proxy: {app.title} can't be reached right now.")
        except LOGIN_ERRORS as err:
            # logins.py has logged it as a warning, once per try (at most once a minute).
            _LOGGER.debug("Logging in to %s refused: %s", app.slug, err)
            return web.Response(
                status=502,
                text=f"Ingress Proxy: Home Assistant couldn't log in to {app.title}. "
                "Check its username and password with Reconfigure.",
            )
        headers = with_authorization(headers, authorization)

        if request.headers.get("Upgrade", "").lower() == "websocket":
            return await self._proxy_websocket(request, app, target, headers)
        return await self._proxy_http(request, app, target, headers)

    def _find_session(self, request: web.Request, slug: str) -> Session | None:
        """The valid panel session the request carries for this app, if any.

        Every value of the session cookie is tried, not only one: a browser can hold the cookie for more than one path
        (an earlier Ingress Proxy kept one at /api/ingress_proxy for every app), and it sends all of them. The Android
        web views of apps like the Lena wall display keep such a cookie for good, since they never end the browsing
        session that would clear it.
        """
        for token in values_named(request.headers.getall("Cookie", []), SESSION_COOKIE):
            session = self.data.sessions.find(token, slug)
            if session is not None:
                return session
        return None

    # Home Assistant adds a route for each of these method names the view has, so every method goes to _proxy.
    get = _proxy
    post = _proxy
    put = _proxy
    patch = _proxy
    delete = _proxy
    head = _proxy
    options = _proxy

    async def _proxy_http(
        self, request: web.Request, app: ProxiedApp, target: str, headers: list[tuple[str, str]]
    ) -> web.StreamResponse:
        """Pass an HTTP request to the app, and stream its reply back as it arrives."""
        client = async_get_client(self.hass, app.verify_ssl)
        try:
            upstream = await client.request(
                request.method,
                target,
                headers=headers,
                data=request.content if request.body_exists else None,
                allow_redirects=False,  # the browser follows redirects itself, through the proxy
            )
        except UPSTREAM_ERRORS as err:
            _LOGGER.debug("%s %s failed: %r", request.method, target, err)
            return web.Response(status=502, text=f"Ingress Proxy: {app.title} can't be reached right now.")

        async with upstream:
            _LOGGER.debug("%s %s -> %s", request.method, target, upstream.status)
            if upstream.status == 401 and app.username and app.login_method == LOGIN_ESPHOME:
                # The Device Builder no longer takes the proxy's token (someone pressed Log out, say). This reply
                # goes through as it is; the next request logs in again.
                self.data.logins.forget(app.slug)
            set_cookies = upstream.headers.getall("Set-Cookie", [])
            out = response_headers(upstream.headers.items(), app)
            if app.persist_login:
                self.data.cookie_jar.update(app.slug, set_cookies)
            else:
                for header in set_cookies:
                    rewritten = rewrite_for_browser(header, app.prefix, app.keep_prefix)
                    if rewritten is not None:
                        out.append(("Set-Cookie", rewritten))

            response = web.StreamResponse(status=upstream.status, reason=upstream.reason, headers=out)
            await response.prepare(request)
            try:
                async for chunk in upstream.content.iter_chunked(STREAM_CHUNK_BYTES):
                    await response.write(chunk)
            except ConnectionError:
                # The browser went away (closed the panel, or navigated off): nothing left to do.
                return response
            except UPSTREAM_ERRORS as err:
                # The app failed part-way. The status line has already gone to the browser, so the honest
                # answer is to cut the connection: the browser then knows the reply is incomplete instead of
                # keeping a truncated page or file as if it were whole.
                _LOGGER.debug("%s %s broke off mid-reply: %r", request.method, target, err)
                if request.transport is not None:
                    request.transport.close()
                return response
            await response.write_eof()
            return response

    async def _proxy_websocket(
        self, request: web.Request, app: ProxiedApp, target: str, headers: list[tuple[str, str]]
    ) -> web.StreamResponse:
        """Open a websocket to the app, accept the browser's, and relay messages both ways until one side closes."""
        # The client makes its own handshake headers for the app; the browser's would clash with them.
        headers = [(name, value) for name, value in headers if not name.lower().startswith("sec-websocket")]
        protocols = [
            protocol.strip()
            for protocol in request.headers.get("Sec-WebSocket-Protocol", "").split(",")
            if protocol.strip()
        ]
        client = async_get_client(self.hass, app.verify_ssl)
        first_message = None
        try:
            # max_msg_size=0: no size limit, so the proxy never refuses a message the app itself accepts.
            upstream = await client.ws_connect(
                websocket_url(target), headers=headers, protocols=protocols, max_msg_size=0
            )
            is_esphome_websocket = urlsplit(target).path.endswith(ESPHOME_WEBSOCKET_PATH)
            if app.username and app.login_method == LOGIN_ESPHOME and is_esphome_websocket:
                upstream, first_message = await self._esphome_logged_in(
                    client, app, target, headers, protocols, upstream
                )
        except UPSTREAM_ERRORS as err:
            # This also covers the app refusing the upgrade (WSServerHandshakeError).
            _LOGGER.debug("Websocket to %s failed: %r", target, err)
            return web.Response(status=502, text=f"Ingress Proxy: {app.title} can't be reached right now.")
        except LOGIN_ERRORS as err:
            _LOGGER.debug("Logging in to %s refused: %s", app.slug, err)
            return web.Response(status=502, text=f"Ingress Proxy: Home Assistant couldn't log in to {app.title}.")

        async with upstream:
            # Offer the browser only the subprotocol the app picked, so both ends agree.
            if upstream.protocol:
                browser_protocols: tuple[str, ...] = (upstream.protocol,)
            else:
                browser_protocols = ()
            browser = web.WebSocketResponse(protocols=browser_protocols, max_msg_size=0)
            await browser.prepare(request)
            _LOGGER.debug("Websocket open: %s", target)
            if first_message is not None:
                await browser.send_str(first_message)
            await _relay(browser, upstream)
            _LOGGER.debug("Websocket closed: %s (browser %s, app %s)", target, browser.close_code, upstream.close_code)
        return browser

    async def _esphome_logged_in(
        self,
        client: aiohttp.ClientSession,
        app: ProxiedApp,
        target: str,
        headers: list[tuple[str, str]],
        protocols: list[str],
        upstream: aiohttp.ClientWebSocketResponse,
    ) -> tuple[aiohttp.ClientWebSocketResponse, str | None]:
        """Make sure an ESPHome Device Builder websocket is logged in, and return it with its first message.

        The Device Builder accepts every websocket, then says in its first message whether the connection still has
        to log in. If it does, the proxy's token is no longer good (someone pressed Log out, say): log in again and
        reconnect once, so the page never shows the login screen. The first message has been read here, so it's
        returned for the relay to send on to the browser.
        """
        message = await upstream.receive()
        if message.type is not WSMsgType.TEXT:
            return upstream, None  # not the server info; nothing to check, and a close is relayed as usual
        if not esphome_wants_login(message.data):
            return upstream, message.data

        await upstream.close()
        self.data.logins.forget(app.slug)
        headers = with_authorization(headers, await self.data.logins.async_authorization(client, app))
        upstream = await client.ws_connect(websocket_url(target), headers=headers, protocols=protocols, max_msg_size=0)
        message = await upstream.receive()
        if message.type is not WSMsgType.TEXT:
            return upstream, None
        return upstream, message.data


async def _relay(browser: web.WebSocketResponse, upstream: aiohttp.ClientWebSocketResponse) -> None:
    """Copy messages both ways until either side closes, then close the other side with the same code."""
    to_app = asyncio.create_task(_pump(browser, upstream))
    to_browser = asyncio.create_task(_pump(upstream, browser))
    try:
        await asyncio.wait({to_app, to_browser}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        # Once one side has closed (or Home Assistant is cancelling this request), the other copy has nothing
        # left to do. Cancel it and wait for both, so neither task outlives the request or leaves an error unread.
        for task in (to_app, to_browser):
            task.cancel()
        results = await asyncio.gather(to_app, to_browser, return_exceptions=True)
        for result in results:
            if isinstance(result, Exception):
                _LOGGER.debug("Websocket relay stopped: %r", result)
        # Whatever happened, neither side is left open.
        await browser.close()
        await upstream.close()


async def _pump(
    source: web.WebSocketResponse | aiohttp.ClientWebSocketResponse,
    destination: web.WebSocketResponse | aiohttp.ClientWebSocketResponse,
) -> None:
    """Copy messages one way until `source` closes, then close `destination` with the code `source` closed with.

    Pings and pongs aren't copied: aiohttp answers them on each connection by itself.
    """
    async for message in source:  # ends when source sends or receives a close
        if message.type is WSMsgType.TEXT:
            await destination.send_str(message.data)
        elif message.type is WSMsgType.BINARY:
            await destination.send_bytes(message.data)
        elif message.type is WSMsgType.ERROR:
            break
    await destination.close(code=close_code_to_send(source.close_code))
