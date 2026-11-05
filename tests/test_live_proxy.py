"""End-to-end check of the proxy with real HTTP and websockets: a browser, the proxy and a fake app, all local.

Run:  pip install aiohttp && python3 tests/test_live_proxy.py

Unlike test_integration.py this needs aiohttp, the library Home Assistant itself runs on. Three servers run on
127.0.0.1: a fake app, Home Assistant's web server with the real proxy view on it, and a browser (an aiohttp
client) that only talks to Home Assistant. Home Assistant itself is still the stand-in from stubs.py.
Prints PASS/FAIL per check and exits non-zero if any check fails.
"""

import asyncio
import gzip
from pathlib import Path
import sys

import aiohttp  # the real one: imported before stubs, so stubs leaves it in place
from aiohttp import web

sys.path.insert(0, str(Path(__file__).resolve().parent))
import stubs  # noqa: E402,I001

import ingress_proxy  # noqa: E402
from ingress_proxy.const import SESSION_COOKIE  # noqa: E402

FAILURES = []
BIG_BODY = bytes(range(256)) * 4096  # 1 MiB
GZIPPED = gzip.compress(b"compressed " * 1000)


def check(name, condition, detail=""):
    """Print one result and remember failures."""
    print(("PASS " if condition else "FAIL ") + name + (f"  ({detail})" if detail and not condition else ""))
    if not condition:
        FAILURES.append(name)


# ---------------------------------------------------------------- the fake app
def make_app(seen):
    """An app with a page for each behaviour the proxy has to get right. `seen` collects what reached it."""
    routes = web.RouteTableDef()

    @routes.get("/")
    async def home(request):
        return web.Response(text="home")

    @routes.get("/login")
    async def login(request):
        # A full URL to the app's own address, as many apps send.
        response = web.Response(status=302, headers={"Location": f"http://{request.host}/dashboard"})
        response.headers.add("Set-Cookie", "sid=abc; Path=/; Priority=High; HttpOnly")
        response.headers.add("Set-Cookie", "lang=en; Path=/")
        return response

    @routes.get("/logout")
    async def logout(request):
        response = web.Response(text="bye")
        response.headers.add("Set-Cookie", "sid=; Path=/; Max-Age=0")
        return response

    @routes.get("/whoami")
    async def whoami(request):
        seen["headers"] = dict(request.headers)
        seen["raw_path"] = request.raw_path
        return web.json_response({"cookies": dict(request.cookies)})

    @routes.get("/odd/{tail:.*}")
    async def odd(request):
        seen["raw_path"] = request.raw_path
        return web.Response(text="ok")

    @routes.post("/echo")
    async def echo(request):
        body = await request.read()
        seen["upload"] = {
            "length": len(body),
            "intact": body == BIG_BODY,
            "content_length": request.headers.get("Content-Length"),
            "chunked": request.headers.get("Transfer-Encoding"),
        }
        return web.Response(text=str(len(body)))

    @routes.get("/gzip")
    async def gzipped(request):
        return web.Response(body=GZIPPED, headers={"Content-Encoding": "gzip", "Content-Type": "text/plain"})

    @routes.get("/stream")
    async def stream(request):
        response = web.StreamResponse()
        await response.prepare(request)
        for index in range(5):
            await response.write(f"part {index}\n".encode())
            await asyncio.sleep(0.02)
        await response.write_eof()
        return response

    @routes.get("/break")
    async def broken(request):
        # Promise 100 kB, send 10 kB, then drop the connection.
        response = web.StreamResponse(headers={"Content-Length": "100000"})
        await response.prepare(request)
        await response.write(b"x" * 10000)
        await asyncio.sleep(0.05)
        request.transport.close()
        return response

    @routes.get("/framed")
    async def framed(request):
        return web.Response(
            text="framed",
            headers={
                "X-Frame-Options": "DENY",
                "Content-Security-Policy": "default-src 'self'; frame-ancestors 'none'",
                "Strict-Transport-Security": "max-age=31536000",
            },
        )

    @routes.get("/ws")
    async def websocket(request):
        ws = web.WebSocketResponse(protocols=("v2.app",))
        await ws.prepare(request)
        seen["ws_protocol"] = ws.ws_protocol
        await ws.send_str("hello from the app")
        async for message in ws:
            if message.type is aiohttp.WSMsgType.TEXT:
                if message.data == "close with 4001":
                    await ws.close(code=4001)
                    break
                await ws.send_str("echo: " + message.data)
            elif message.type is aiohttp.WSMsgType.BINARY:
                await ws.send_bytes(message.data[::-1])
        seen["ws_close_code"] = ws.close_code
        return ws

    @routes.get("/api")
    async def token_websocket(request):
        # Like Zigbee2MQTT's frontend server with an auth_token: accept the websocket, then close it with 4401
        # unless the token is in the query string. The web page shows its login form only on that close code.
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        if request.query.get("token") != "z2m-secret":
            await ws.close(code=4401, message=b"Unauthorized")
            return ws
        await ws.send_str('{"topic": "bridge/state", "payload": {"state": "online"}}')
        await ws.close()
        return ws

    app = web.Application(client_max_size=8 * 1024 * 1024)  # aiohttp's default (1 MiB) would refuse BIG_BODY
    app.add_routes(routes)
    return app


# ---------------------------------------------------------------- Home Assistant's web server, with the view
def make_home_assistant_server(view):
    """Route the view's methods the way Home Assistant's register_view does."""
    app = web.Application()
    for url in [view.url, *view.extra_urls]:
        for method in ("get", "post", "put", "patch", "delete", "head", "options"):
            handler = getattr(view, method)

            async def route(request, handler=handler):
                return await handler(request, **request.match_info)

            app.router.add_route(method.upper(), url, route)
    return app


async def start(app):
    """Serve an aiohttp app on a free local port and return (runner, base URL)."""
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    return runner, f"http://127.0.0.1:{port}"


def entry(slug, url, **changes):
    """A saved app, as the setup form writes it."""
    data = {
        "slug": slug,
        "title": slug.title(),
        "url": url,
        "icon": "mdi:application",
        "require_admin": True,
        "persist_login": True,
        "keep_prefix": False,
        "verify_ssl": True,
        "headers": {"X-App-Token": "from-settings"},
        "local_storage": {},
    }
    data.update(changes)
    return stubs.ConfigEntry(data, entry_id=slug)


async def main():
    """Start everything, then act as the browser."""
    seen = {}
    app_runner, app_url = await start(make_app(seen))
    clients = []

    def client_factory(verify_ssl, **kwargs):
        client = aiohttp.ClientSession(**kwargs)
        clients.append(client)
        return client

    hass = stubs.HomeAssistant()
    hass.data["test_client_factory"] = client_factory
    await ingress_proxy.async_setup(hass, {})
    await ingress_proxy.async_setup_entry(hass, entry("app", app_url))
    await ingress_proxy.async_setup_entry(hass, entry("open", app_url, persist_login=False, require_admin=False))
    await ingress_proxy.async_setup_entry(hass, entry("down", "http://127.0.0.1:9"))
    data = hass.data["ingress_proxy"]
    ha_runner, ha_url = await start(make_home_assistant_server(stubs.RECORDED["views"][0]))

    browser = aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True), auto_decompress=False)
    base = f"{ha_url}/api/ingress_proxy"
    try:
        async with browser.get(f"{base}/app/") as response:
            check("no session: 401", response.status == 401)
        async with browser.ws_connect(f"{base}/app/ws") as _:
            check("no session: websocket refused", False)
    except aiohttp.WSServerHandshakeError as err:
        check("no session: websocket refused", err.status == 401)

    try:
        # What the panel does: get a session and store it as a cookie for the app's path.
        session = data.sessions.create("app", is_admin=True)
        browser.cookie_jar.update_cookies({SESSION_COOKIE: session}, response_url=aiohttp.client.URL(f"{base}/app/"))
        browser.cookie_jar.update_cookies({"browser_pref": "1"}, response_url=aiohttp.client.URL(f"{base}/app/"))

        async with browser.get(f"{base}/app", allow_redirects=False) as response:
            check(
                "bare prefix: redirect to the trailing /",
                response.status == 307 and response.headers["Location"] == "/api/ingress_proxy/app/",
            )
        async with browser.get(f"{base}/app/") as response:
            check("front page", response.status == 200 and await response.text() == "home")
        # An older version's session cookie, kept at /api/ingress_proxy, is sent along with this app's own.
        stale = f"{SESSION_COOKIE}={session}; {SESSION_COOKIE}=left-over"
        plain = aiohttp.ClientSession(cookie_jar=aiohttp.DummyCookieJar())  # sends exactly the Cookie header given
        try:
            async with plain.get(f"{base}/app/", headers={"Cookie": stale}) as response:
                check(
                    "a stale session cookie sent last doesn't lock the app out", response.status == 200, response.status
                )
        finally:
            await plain.close()

        async with browser.get(f"{base}/app/login", allow_redirects=False) as response:
            check(
                "login redirect rewritten",
                response.status == 302 and response.headers["Location"] == "/api/ingress_proxy/app/dashboard",
                response.headers.get("Location"),
            )
            check("login cookies kept away from the browser", "Set-Cookie" not in response.headers)
        check(
            "login cookies kept in Home Assistant, Priority not taken for a cookie",
            data.cookie_jar.cookies_for("app") == {"sid": "abc", "lang": "en"},
            data.cookie_jar.cookies_for("app"),
        )

        async with browser.get(f"{base}/app/whoami") as response:
            body = await response.json()
        headers = seen["headers"]
        check(
            "app gets the kept and the browser's cookies, not the session",
            body["cookies"] == {"browser_pref": "1", "sid": "abc", "lang": "en"},
            body,
        )
        check("app gets its own address as Host", headers.get("Host") == app_url.removeprefix("http://"))
        check(
            "app gets the prefix and forwarded headers",
            headers.get("X-Ingress-Path") == "/api/ingress_proxy/app"
            and headers.get("X-Forwarded-Host") == ha_url.removeprefix("http://"),
        )
        check("app gets the token from the settings", headers.get("X-App-Token") == "from-settings")
        check("app gets no Origin or Referer", "Origin" not in headers and "Referer" not in headers)

        async with browser.get(f"{base}/app/odd/a%2Fb%20c;x=1?q=%26&r=1") as response:
            check(
                "odd path and query reach the app exactly",
                seen["raw_path"] == "/odd/a%2Fb%20c;x=1?q=%26&r=1",
                seen["raw_path"],
            )
        # encoded=True: sent exactly as written (a browser would tidy it away, but a hand-made request needn't).
        async with browser.get(aiohttp.client.URL(f"{base}/app/odd/%2e%2e/secret", encoded=True)) as response:
            check("encoded .. refused", response.status == 400)

        async with browser.post(f"{base}/app/echo", data=BIG_BODY) as response:
            await response.read()
            check("1 MiB upload accepted", response.status == 200, response.status)
        upload = seen.get("upload", {})
        check("1 MiB upload arrives whole", upload.get("intact"), upload)
        check(
            "upload keeps its Content-Length (not chunked)",
            upload.get("content_length") == str(len(BIG_BODY)) and upload.get("chunked") is None,
            upload,
        )

        async with browser.get(f"{base}/app/gzip", headers={"Accept-Encoding": "gzip"}) as response:
            raw = await response.read()
            check(
                "gzip passes through untouched", raw == GZIPPED and response.headers.get("Content-Encoding") == "gzip"
            )
            check("gzip keeps its Content-Length", response.headers.get("Content-Length") == str(len(GZIPPED)))
        async with browser.head(f"{base}/app/gzip") as response:
            check("HEAD works", response.status == 200 and response.headers.get("Content-Length") == str(len(GZIPPED)))

        async with browser.get(f"{base}/app/stream") as response:
            parts = []
            async for chunk in response.content.iter_any():
                parts.append(chunk)
            text = b"".join(parts).decode()
            check("streamed reply arrives whole", text == "".join(f"part {i}\n" for i in range(5)), text)
            check("streamed reply arrives in pieces", len(parts) > 1, len(parts))

        outcome = "complete"
        try:
            # Left open, the browser would wait forever for the rest; the timeout turns that into a failure.
            async with browser.get(f"{base}/app/break", timeout=aiohttp.ClientTimeout(total=5)) as response:
                await response.read()
        except aiohttp.ClientPayloadError:
            outcome = "cut off"
        except TimeoutError:
            outcome = "left hanging"
        check("a reply the app broke off is cut off for the browser too", outcome == "cut off", outcome)

        async with browser.get(f"{base}/app/framed") as response:
            check(
                "X-Frame-Options and HSTS dropped",
                "X-Frame-Options" not in response.headers and "Strict-Transport-Security" not in response.headers,
            )
            check(
                "frame-ancestors dropped from the policy",
                response.headers.get("Content-Security-Policy") == "default-src 'self'",
            )

        async with browser.ws_connect(f"{base}/app/ws", protocols=("v1.app", "v2.app")) as ws:
            check(
                "websocket: subprotocol agreed end to end",
                ws.protocol == "v2.app" and seen["ws_protocol"] == "v2.app",
                ws.protocol,
            )
            first = await ws.receive(timeout=2)
            check("websocket: app speaks first", first.data == "hello from the app")
            await ws.send_str("hi")
            check("websocket: text both ways", (await ws.receive(timeout=2)).data == "echo: hi")
            await ws.send_bytes(b"\x00\x01\x02")
            check("websocket: binary both ways", (await ws.receive(timeout=2)).data == b"\x02\x01\x00")
            await ws.close(code=4000)
        await asyncio.sleep(0.1)
        check(
            "websocket: browser's close code reaches the app",
            seen.get("ws_close_code") == 4000,
            seen.get("ws_close_code"),
        )

        async with browser.ws_connect(f"{base}/app/ws") as ws:
            await ws.receive(timeout=2)
            await ws.send_str("close with 4001")
            message = await ws.receive(timeout=2)
            check(
                "websocket: app's close code reaches the browser",
                message.type is aiohttp.WSMsgType.CLOSE and ws.close_code == 4001,
                (message.type, ws.close_code),
            )

        async with browser.ws_connect(f"{base}/app/api") as ws:
            message = await ws.receive(timeout=2)
            check(
                "token websocket: 4401 reaches the page, so it asks for the token", ws.close_code == 4401, ws.close_code
            )
        async with browser.ws_connect(f"{base}/app/api?token=z2m-secret") as ws:
            message = await ws.receive(timeout=2)
            check("token websocket: the token in the query reaches the app", "online" in str(message.data), message)

        async with browser.get(f"{base}/app/logout") as response:
            await response.read()
        check(
            "logout deletes the kept cookie",
            data.cookie_jar.cookies_for("app") == {"lang": "en"},
            data.cookie_jar.cookies_for("app"),
        )

        # An app with persist login off: the browser keeps its cookies, under the app's path.
        open_session = data.sessions.create("open", is_admin=False)
        browser.cookie_jar.update_cookies(
            {SESSION_COOKIE: open_session}, response_url=aiohttp.client.URL(f"{base}/open/")
        )
        async with browser.get(f"{base}/open/login", allow_redirects=False) as response:
            cookies = response.headers.getall("Set-Cookie", [])
            check(
                "persist login off: cookies go to the browser, under the app's path",
                "sid=abc; Path=/api/ingress_proxy/open; Priority=High; HttpOnly" in cookies,
                cookies,
            )
        async with browser.get(f"{base}/open/whoami") as response:
            body = await response.json()
            check("persist login off: the browser sends them back", body["cookies"].get("sid") == "abc", body)
        check("persist login off: Home Assistant keeps none", data.cookie_jar.cookies_for("open") == {})

        down_session = data.sessions.create("down", is_admin=True)
        browser.cookie_jar.update_cookies(
            {SESSION_COOKIE: down_session}, response_url=aiohttp.client.URL(f"{base}/down/")
        )
        async with browser.get(f"{base}/down/") as response:
            check("app down: 502", response.status == 502)
        try:
            async with browser.ws_connect(f"{base}/down/ws"):
                check("app down: websocket 502", False)
        except aiohttp.WSServerHandshakeError as err:
            check("app down: websocket 502", err.status == 502)
    finally:
        await browser.close()
        for client in clients:
            await client.close()
        await ha_runner.cleanup()
        await app_runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED: " + ", ".join(FAILURES))
        sys.exit(1)
    print("All checks passed.")
