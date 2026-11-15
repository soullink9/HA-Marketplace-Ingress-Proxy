"""Checks for the Ingress Proxy integration that need nothing but Python.

Run:  python3 tests/test_integration.py

The real integration files run against the stand-ins in stubs.py and a fake app. Covered: every rewrite rule,
cookie handling, sessions, the setup form, set-up and removal of apps, the session command, and the proxy view's
decisions (who is let in, what is sent to the app and what comes back). Websocket relaying and real HTTP are
covered by test_live_proxy.py, which needs aiohttp. Prints PASS/FAIL per check and exits non-zero if any check fails.
"""

import asyncio
from datetime import UTC, datetime
import json
from pathlib import Path
import sys
import types

sys.path.insert(0, str(Path(__file__).resolve().parent))
import stubs  # noqa: E402,I001  (registers the stand-ins before the integration is imported)

import aiohttp  # noqa: E402  (the stand-in)

import ingress_proxy  # noqa: E402
from ingress_proxy import config_flow, cookies, logins, proxy, rewrite, sessions, websocket  # noqa: E402
from ingress_proxy.client import async_get_client  # noqa: E402
from ingress_proxy.const import SESSION_COOKIE, SESSION_TTL_SECONDS  # noqa: E402
from ingress_proxy.models import ProxiedApp  # noqa: E402

FAILURES = []


def check(name, condition, detail=""):
    """Print one result and remember failures."""
    print(("PASS " if condition else "FAIL ") + name + (f"  ({detail})" if detail and not condition else ""))
    if not condition:
        FAILURES.append(name)


MUSIC = ProxiedApp(slug="music", title="Music", url="http://music:8095")
MUSIC_PREFIXED = ProxiedApp(slug="music", title="Music", url="http://music:8095", keep_prefix=True)
WITH_PATH = ProxiedApp(slug="z2m", title="Zigbee", url="http://z2m:8080/ui")
WITH_TOKEN = ProxiedApp(slug="api", title="API", url="http://api:80", headers={"Authorization": "Bearer abc"})
WITH_LOGIN = ProxiedApp(slug="esp", title="ESPHome", url="http://esphome:6052", username="admin", password="pa:ss é")
ESPHOME = ProxiedApp(
    slug="esp", title="ESPHome", url="http://esphome:6052", username="admin", password="hunter2", login_method="esphome"
)


# ---------------------------------------------------------------- rewrite.py
def test_rewrite():
    """Every rule for what passes between the browser and the app."""
    check("url: path after the prefix", rewrite.upstream_url(MUSIC, "/a/b", "") == "http://music:8095/a/b")
    check("url: empty tail is /", rewrite.upstream_url(MUSIC, "", "") == "http://music:8095/")
    check(
        "url: encoding and query kept exactly",
        rewrite.upstream_url(MUSIC, "/a%2Fb/c%20d", "x=1&y=%26") == "http://music:8095/a%2Fb/c%20d?x=1&y=%26",
    )
    check(
        "url: keep_prefix sends the prefix",
        rewrite.upstream_url(MUSIC_PREFIXED, "/a", "") == "http://music:8095/api/ingress_proxy/music/a",
    )
    check("url: app with a path of its own", rewrite.upstream_url(WITH_PATH, "/x", "") == "http://z2m:8080/ui/x")

    check("parent: plain ..", rewrite.has_parent_segment("/a/../b"))
    check("parent: encoded %2e%2e", rewrite.has_parent_segment("/a/%2e%2E/b"))
    check("parent: ... and a..b are fine", not rewrite.has_parent_segment("/.../a..b/"))

    incoming = [
        ("Host", "ha.example"),
        ("Cookie", "ingress_proxy_session=x"),
        ("Origin", "https://ha.example"),
        ("Referer", "https://ha.example/proxy-music"),
        ("Connection", "keep-alive"),
        ("X-Forwarded-For", "6.6.6.6"),
        ("Accept", "text/html"),
        ("Accept", "image/png"),
        ("Accept-Encoding", "gzip, br"),
        ("Content-Length", "12"),
        ("authorization", "Basic old"),
    ]
    out = rewrite.request_headers(incoming, WITH_TOKEN, {"sid": "1", "lang": "en"}, "ha.example", "https", "10.0.0.5")
    names = [name.lower() for name, _ in out]
    check(
        "request: host, origin, referer, hop-by-hop dropped",
        not {"host", "origin", "referer", "connection"} & set(names),
    )
    check(
        "request: spoofed X-Forwarded-For replaced",
        ("X-Forwarded-For", "10.0.0.5") in out and ("X-Forwarded-For", "6.6.6.6") not in out,
    )
    check("request: repeated headers kept", names.count("accept") == 2)
    check(
        "request: compression and length passed through",
        ("Accept-Encoding", "gzip, br") in out and ("Content-Length", "12") in out,
    )
    check("request: cookies rebuilt", ("Cookie", "sid=1; lang=en") in out and names.count("cookie") == 1)
    check(
        "request: app headers replace the browser's, any case",
        ("Authorization", "Bearer abc") in out and names.count("authorization") == 1,
    )
    check(
        "request: prefix headers",
        ("X-Ingress-Path", "/api/ingress_proxy/api") in out and ("X-Forwarded-Prefix", "/api/ingress_proxy/api") in out,
    )
    check(
        "request: forwarded host and scheme",
        ("X-Forwarded-Host", "ha.example") in out and ("X-Forwarded-Proto", "https") in out,
    )
    no_cookies = rewrite.request_headers([], MUSIC, {}, "h", "http", None)
    check(
        "request: no Cookie or X-Forwarded-For when there are none",
        not any(n in ("Cookie", "X-Forwarded-For") for n, _ in no_cookies),
    )

    reply = [
        ("Content-Type", "text/html"),
        ("Content-Length", "100"),
        ("Content-Encoding", "gzip"),
        ("Set-Cookie", "a=1"),
        ("X-Frame-Options", "DENY"),
        ("Strict-Transport-Security", "max-age=1"),
        ("Clear-Site-Data", '"*"'),
        ("Service-Worker-Allowed", "/"),
        ("Transfer-Encoding", "chunked"),
        ("Location", "http://music:8095/login"),
        ("Content-Security-Policy", "default-src 'self'; frame-ancestors 'none'"),
        ("Link", "</a.css>; rel=preload"),
        ("Link", "</b.js>; rel=preload"),
    ]
    out = rewrite.response_headers(reply, MUSIC)
    names = [name.lower() for name, _ in out]
    check(
        "response: headers that would act on Home Assistant dropped",
        not {
            "set-cookie",
            "x-frame-options",
            "strict-transport-security",
            "clear-site-data",
            "service-worker-allowed",
            "transfer-encoding",
        }
        & set(names),
    )
    check(
        "response: length and encoding passed through",
        ("Content-Length", "100") in out and ("Content-Encoding", "gzip") in out,
    )
    check("response: Location rewritten", ("Location", "/api/ingress_proxy/music/login") in out)
    check("response: frame-ancestors removed, rest kept", ("Content-Security-Policy", "default-src 'self'") in out)
    check("response: repeated headers kept", names.count("link") == 2)
    only_frame = rewrite.response_headers([("Content-Security-Policy", "frame-ancestors 'self'")], MUSIC)
    check("response: a policy of only frame-ancestors is dropped", only_frame == [])

    cases = [
        (MUSIC, "/login", "/api/ingress_proxy/music/login"),
        (MUSIC, "http://music:8095/a?b=1", "/api/ingress_proxy/music/a?b=1"),
        (MUSIC, "http://music:8095", "/api/ingress_proxy/music/"),
        (MUSIC, "/api/ingress_proxy/music/x", "/api/ingress_proxy/music/x"),
        (MUSIC, "https://elsewhere.example/x", "https://elsewhere.example/x"),
        (MUSIC, "//elsewhere.example/x", "//elsewhere.example/x"),
        (MUSIC, "next", "next"),
        (MUSIC, "http://music:80950/x", "http://music:80950/x"),
        (MUSIC_PREFIXED, "/api/ingress_proxy/music/x", "/api/ingress_proxy/music/x"),
        (MUSIC_PREFIXED, "http://music:8095/api/ingress_proxy/music/x", "/api/ingress_proxy/music/x"),
        (WITH_PATH, "/ui/settings", "/api/ingress_proxy/z2m/settings"),
        (WITH_PATH, "http://z2m:8080/ui/a", "/api/ingress_proxy/z2m/a"),
        (WITH_PATH, "/other", "/api/ingress_proxy/z2m/other"),
    ]
    for app, location, expected in cases:
        got = rewrite.rewrite_location(location, app)
        check(f"location: {app.slug} {location}", got == expected, got)

    check("ws url: http", rewrite.websocket_url("http://a:1/ws?x") == "ws://a:1/ws?x")
    check("ws url: https", rewrite.websocket_url("https://a/ws") == "wss://a/ws")

    browser_auth = [("Accept", "*/*"), ("authorization", "Basic from-the-browser")]
    check(
        "authorization: no login leaves headers alone", rewrite.with_authorization(browser_auth, None) is browser_auth
    )
    check(
        "authorization: the login replaces the browser's",
        rewrite.with_authorization(browser_auth, "Basic login")
        == [("Accept", "*/*"), ("Authorization", "Basic login")],
    )
    challenge = [("WWW-Authenticate", 'Basic realm="app"'), ("Content-Type", "text/plain")]
    check(
        "www-authenticate: passed on when Home Assistant doesn't log in",
        ("WWW-Authenticate", 'Basic realm="app"') in rewrite.response_headers(challenge, MUSIC),
    )
    check(
        "www-authenticate: dropped when Home Assistant logs in, so the browser shows no login box",
        rewrite.response_headers(challenge, WITH_LOGIN) == [("Content-Type", "text/plain")],
    )
    for code, expected in (
        (1000, 1000),
        (1001, 1001),
        (1005, 1000),
        (1006, 1000),
        (1011, 1011),
        (4000, 4000),
        (None, 1000),
        (2000, 1000),
    ):
        check(f"close code {code} -> {expected}", rewrite.close_code_to_send(code) == expected)


# ---------------------------------------------------------------- cookies.py
def test_cookies():
    """Set-Cookie parsing, the browser rewrite, and the kept-cookie jar."""
    now = datetime(2026, 11, 1, tzinfo=UTC)
    cookie = cookies.SetCookie.parse("sid=abc; Path=/; Expires=Wed, 21 Oct 2037 07:28:00 GMT; Priority=High; HttpOnly")
    check("parse: one cookie despite Priority", cookie.name == "sid" and cookie.value == "abc")
    check("parse: attributes kept", cookie.get("priority") == "High" and cookie.get("HttpOnly") == "")
    check(
        "parse: not a cookie",
        cookies.SetCookie.parse("no equals sign") is None and cookies.SetCookie.parse("=x") is None,
    )
    check("deletion: future expiry is not", not cookie.is_deletion(now))
    check("deletion: empty value", cookies.SetCookie.parse("sid=; Path=/").is_deletion(now))
    check("deletion: Max-Age=0", cookies.SetCookie.parse("sid=a; Max-Age=0").is_deletion(now))
    check(
        "deletion: Expires in the past",
        cookies.SetCookie.parse("sid=a; Expires=Thu, 01 Jan 1970 00:00:00 GMT").is_deletion(now),
    )
    check(
        "deletion: Max-Age wins over Expires",
        not cookies.SetCookie.parse("sid=a; Max-Age=60; Expires=Thu, 01 Jan 1970 00:00:00 GMT").is_deletion(now),
    )
    check("deletion: bad Expires is not", not cookies.SetCookie.parse("sid=a; Expires=soon").is_deletion(now))

    rewritten = cookies.rewrite_for_browser(
        "sid=1; Domain=music; Path=/; HttpOnly; SameSite=Lax", "/api/ingress_proxy/music", False
    )
    check(
        "browser: domain dropped, path moved",
        rewritten == "sid=1; Path=/api/ingress_proxy/music; HttpOnly; SameSite=Lax",
        rewritten,
    )
    rewritten = cookies.rewrite_for_browser("sid=1; path=/app", "/api/ingress_proxy/music", False)
    check(
        "browser: sub-path moved under the prefix", rewritten == "sid=1; path=/api/ingress_proxy/music/app", rewritten
    )
    rewritten = cookies.rewrite_for_browser("sid=1; Path=/api/ingress_proxy/music", "/api/ingress_proxy/music", True)
    check("browser: keep_prefix leaves the path", rewritten == "sid=1; Path=/api/ingress_proxy/music", rewritten)
    check("browser: no Path stays without one", cookies.rewrite_for_browser("a=1", "/p", False) == "a=1")
    check(
        "cookie values: every value of a name, in order",
        cookies.values_named(["a=1; s=new; b=2; s=old", "s=third"], "s") == ["new", "old", "third"],
    )
    check("cookie values: none", cookies.values_named([], "s") == [] and cookies.values_named(["sx=1; x=s"], "s") == [])
    check(
        "browser: the proxy's own cookie can't be overwritten",
        cookies.rewrite_for_browser(f"{SESSION_COOKIE}=evil", "/p", False) is None,
    )

    async def jar_checks():
        store = stubs.Store(None, 1, "jar-test")
        jar = cookies.UpstreamCookieJar(store)
        await jar.async_load()
        check("jar: empty to start", jar.cookies_for("music") == {})
        jar.update("music", ["sid=1; Path=/", "lang=en", f"{SESSION_COOKIE}=evil", "junk"], now)
        check("jar: kept", jar.cookies_for("music") == {"sid": "1", "lang": "en"})
        check(
            "jar: one save for a login's cookies",
            store.saves == 1 and store.saved == {"music": {"sid": "1", "lang": "en"}},
        )
        jar.cookies_for("music")["sid"] = "changed"
        check("jar: callers get a copy", jar.cookies_for("music")["sid"] == "1")
        jar.update("music", [], now)
        check("jar: no Set-Cookie, no save", store.saves == 1)
        jar.update("music", ["sid=; Max-Age=0", "lang=; Max-Age=0"], now)
        check("jar: deleted cookies forgotten, app removed", jar.cookies_for("music") == {} and store.saved == {})
        jar.update("music", ["sid=2"], now)
        jar.update("other", ["t=9"], now)
        jar.forget_app("music")
        check("jar: forget one app", store.saved == {"other": {"t": "9"}})
        saves = store.saves
        jar.forget_app("music")
        check("jar: forgetting an unknown app doesn't save", store.saves == saves)
        reloaded = cookies.UpstreamCookieJar(store)
        await reloaded.async_load()
        check("jar: survives a restart", reloaded.cookies_for("other") == {"t": "9"})

    asyncio.run(jar_checks())


# ---------------------------------------------------------------- sessions.py
def test_sessions():
    """Tokens open one app, run out, and are cleaned up."""
    clock = {"now": 1000.0}
    store = sessions.SessionStore(clock=lambda: clock["now"])
    token = store.create("music", is_admin=False)
    check(
        "session: found for its app",
        store.find(token, "music") == sessions.Session("music", False, 1000.0 + SESSION_TTL_SECONDS),
    )
    check("session: not for another app", store.find(token, "z2m") is None)
    check("session: missing token", store.find(None, "music") is None and store.find("", "music") is None)
    check("session: unknown token", store.find("made-up", "music") is None)
    check("session: tokens differ", store.create("music", False) != token)
    clock["now"] += SESSION_TTL_SECONDS
    check("session: runs out", store.find(token, "music") is None)
    store.create("z2m", True)
    check("session: expired ones cleaned up on create", len(store) == 1)
    second = store.create("music", True)
    store.forget_app("z2m")
    check("session: forget one app", len(store) == 1 and store.find(second, "music") is not None)


# ---------------------------------------------------------------- logins.py
def test_logins():
    """HTTP Basic, and logging in to an ESPHome Device Builder for its token."""
    check(
        "basic: username:password in UTF-8, base64",
        logins.basic_authorization("admin", "pa:ss é") == "Basic YWRtaW46cGE6c3Mgw6k=",
    )
    check("password left out of the app's repr", "pa:ss" not in repr(WITH_LOGIN))
    check("esphome: server info asking for a login", logins.esphome_wants_login('{"requires_auth": true}'))
    for message in ('{"requires_auth": false}', "{}", "[]", "not json", '{"requires_auth": "yes"}'):
        check(f"esphome: no login asked by {message!r}", not logins.esphome_wants_login(message))

    async def checks():
        store = logins.UpstreamLogins()
        client = FakeClient()
        check("no username: no login", await store.async_authorization(client, MUSIC) is None)
        check(
            "basic: sent as is",
            await store.async_authorization(client, WITH_LOGIN) == "Basic YWRtaW46cGE6c3Mgw6k=",
        )
        check("basic: no websocket needed", client.websockets == [])

        client.esphome = FakeEsphome()
        results = await asyncio.gather(*(store.async_authorization(client, ESPHOME) for _ in range(5)))
        check("esphome: bearer token", results == ["Bearer token1"] * 5, results)
        check("esphome: one login for many requests at once", client.esphome.logins == [("admin", "hunter2")])
        check("esphome: logged in on its /ws", client.websockets == ["ws://esphome:6052/ws"], client.websockets)
        store.forget("esp")
        check("esphome: forget logs in again", await store.async_authorization(client, ESPHOME) == "Bearer token2")

        client.esphome = FakeEsphome(password="other")
        store.forget("esp")
        try:
            await store.async_authorization(client, ESPHOME)
            check("esphome: wrong password refused", False)
        except logins.LoginRefused as err:
            check("esphome: wrong password refused, without saying it", "hunter2" not in str(err), str(err))

        client.esphome = FakeEsphome()
        try:
            await store.async_authorization(client, ESPHOME)
            check("esphome: a refusal isn't retried straight away", False)
        except logins.LoginRefused:
            check("esphome: a refusal isn't retried straight away", client.esphome.logins == [])
        store.forget("esp")
        check(
            "esphome: forget tries again at once", await store.async_authorization(client, ESPHOME) == "Bearer token1"
        )

        store.forget("esp")
        client.esphome = FakeEsphome(password=None)
        check("esphome: no password set, nothing to send", await store.async_authorization(client, ESPHOME) is None)

        for esphome, name in ((FakeEsphome(speaks=False), "speaks something else"), (None, "no websocket")):
            client.esphome = esphome
            store.forget("esp")
            try:
                await store.async_authorization(client, ESPHOME)
                check(f"esphome: not a Device Builder ({name})", False)
            except logins.NotEsphome:
                check(f"esphome: not a Device Builder ({name})", True)

    asyncio.run(checks())


# ---------------------------------------------------------------- a fake app, as the proxy's client sees it
class FakeHeaders:
    """Headers as a list of pairs, with aiohttp's getall."""

    def __init__(self, pairs):
        self.pairs = list(pairs)

    def items(self):
        return list(self.pairs)

    def get(self, name, default=None):
        for key, value in self.pairs:
            if key.lower() == name.lower():
                return value
        return default

    def getall(self, name, default=None):
        values = [value for key, value in self.pairs if key.lower() == name.lower()]
        return values or (default if default is not None else [])


class FakeUpstream:
    """The app's reply: status, headers, and a body that can break off part-way."""

    def __init__(self, status=200, headers=(), chunks=(b"hello",), fail_after=None):
        self.status = status
        self.reason = "OK"
        self.headers = FakeHeaders(headers)
        self._chunks = list(chunks)
        self._fail_after = fail_after
        self.released = False
        self.content = types.SimpleNamespace(iter_chunked=self._iter_chunked)

    async def _iter_chunked(self, size):
        for index, chunk in enumerate(self._chunks):
            if self._fail_after is not None and index >= self._fail_after:
                raise aiohttp.ClientError("the app broke off")
            yield chunk

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        self.released = True


class FakeClient:
    """Records each request and answers with the next prepared reply (or raises it, if it's an exception)."""

    def __init__(self):
        self.closed = False
        self.requests = []
        self.replies = []
        self.esphome = None  # a FakeEsphome answers websockets; None: no websocket at all (a 404 to the upgrade)
        self.websockets = []

    def ws_connect(self, url, **kwargs):
        self.websockets.append(url)
        if self.esphome is None:

            class Refused:
                async def __aenter__(self):
                    raise aiohttp.WSServerHandshakeError(status=404)

                async def __aexit__(self, *exc):
                    return False

            return Refused()
        return self.esphome.connect()

    async def request(self, method, url, headers, data, allow_redirects):
        self.requests.append({"method": method, "url": url, "headers": headers, "data": data})
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    def get(self, url, headers, allow_redirects, timeout):
        self.requests.append({"method": "GET", "url": url, "headers": headers})
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):

            class Failing:
                async def __aenter__(self):
                    raise reply

                async def __aexit__(self, *exc):
                    return False

            return Failing()
        return reply


class FakeEsphome:
    """An ESPHome Device Builder's websocket login, as its /ws answers it.

    `password` None: no password set, so every connection is logged in from the start. `speaks` False: something
    that isn't a Device Builder accepts the websocket and says something else.
    """

    def __init__(self, password="hunter2", speaks=True):
        self.password = password
        self.speaks = speaks
        self.logins = []  # the (username, password) of each login command
        self.tokens = []  # the tokens handed out

    def connect(self):
        """A new websocket: the server info comes first, as the Device Builder sends it."""
        socket = FakeEsphomeSocket(self)
        if self.speaks:
            socket.inbox.append(json.dumps({"server_version": "1.28.1", "requires_auth": self.password is not None}))
        else:
            socket.inbox.append("hello")
        return socket


class FakeEsphomeSocket:
    """One websocket to the FakeEsphome."""

    def __init__(self, esphome):
        self.esphome = esphome
        self.inbox = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def receive(self):
        return types.SimpleNamespace(type=aiohttp.WSMsgType.TEXT, data=self.inbox.pop(0))

    async def send_str(self, text):
        command = json.loads(text)
        args = command["args"]
        self.esphome.logins.append((args["username"], args["password"]))
        # Something else first, to show the proxy waits for the reply to its own command.
        self.inbox.append(json.dumps({"message_id": "other", "event": "noise"}))
        if args["username"] == "admin" and args["password"] == self.esphome.password:
            token = f"token{len(self.esphome.tokens) + 1}"
            self.esphome.tokens.append(token)
            self.inbox.append(json.dumps({"message_id": command["message_id"], "result": {"token": token}}))
        else:
            self.inbox.append(
                json.dumps(
                    {"message_id": command["message_id"], "error_code": "not_authenticated", "details": "Invalid"}
                )
            )


class FakeRequest:
    """A browser request reaching the proxy view.

    `cookies` become the Cookie header the way a browser sends them; `cookie_header` gives the header as is (for a
    cookie name sent twice). `cookies` the attribute is what aiohttp makes of the header: the last value per name.
    """

    def __init__(
        self, raw_path, query="", cookies=None, headers=(), method="GET", browser_leaves_after=None, cookie_header=None
    ):
        self.method = method
        self.path = raw_path
        self.rel_url = types.SimpleNamespace(raw_path=raw_path, raw_query_string=query)
        if cookie_header is None:
            cookie_header = "; ".join(f"{name}={value}" for name, value in (cookies or {}).items())
        self.cookies = {}
        for pair in cookie_header.split(";"):
            name, equals, value = pair.partition("=")
            if equals:
                self.cookies[name.strip()] = value.strip()
        headers = list(headers)
        if cookie_header:
            headers.append(("Cookie", cookie_header))
        self.headers = FakeHeaders(headers)
        self.host = "ha.example"
        self.scheme = "https"
        self.remote = "10.0.0.5"
        self.body_exists = method in ("POST", "PUT")
        self.content = "request body stream"
        self.transport = types.SimpleNamespace(closed=False)
        self.transport.close = lambda: setattr(self.transport, "closed", True)
        self.browser_leaves_after = browser_leaves_after


def new_hass(client):
    """A fresh Home Assistant stand-in whose client sessions are `client`."""
    hass = stubs.HomeAssistant()
    hass.data["test_client_factory"] = lambda verify_ssl, **kwargs: client
    for key in ("panels", "stores"):
        stubs.RECORDED[key].clear()
    for key in ("removed_panels", "views", "static_paths", "ws_commands", "clients"):
        stubs.RECORDED[key].clear()
    return hass


def entry_data(**changes):
    """A saved app, as the setup form writes it."""
    data = {
        "slug": "music",
        "title": "Music",
        "url": "http://music:8095",
        "icon": "mdi:music",
        "require_admin": True,
        "persist_login": True,
        "keep_prefix": False,
        "verify_ssl": True,
        "headers": {},
        "local_storage": {"ma_access_token": "secret"},
    }
    data.update(changes)
    return data


# ---------------------------------------------------------------- config_flow.py
def test_config_flow():
    """The form tidies and checks what's typed, and saves flat data."""
    check("clean url: trailing / and spaces", config_flow.clean_url("  http://music:8095/ ") == "http://music:8095")
    check("clean url: https with a path", config_flow.clean_url("https://z2m.lan/ui/") == "https://z2m.lan/ui")
    for bad in ("music:8095", "ftp://x", "http://", "http://x/?a=1", "http://x/#f", "http://u:p@x"):
        check(f"clean url refuses {bad!r}", config_flow.clean_url(bad) is None)
    check("clean slug: fine", config_flow.clean_slug(" music_assistant ") == "music_assistant")
    for bad in ("Music", "music-assistant", "a b", "", "_x"):
        check(f"clean slug refuses {bad!r}", config_flow.clean_slug(bad) is None)
    check("headers: empty", config_flow.clean_headers(None) == {} and config_flow.clean_headers("") == {})
    check(
        "headers: numbers become text",
        config_flow.clean_headers({"X-Api-Key": 123, "X-On": True}) == {"X-Api-Key": "123", "X-On": "True"},
    )
    for bad in ({"Bad Name": "x"}, {"X": "a\r\nInjected: 1"}, {"X": ["a"]}, {"X": None}, ["X: 1"]):
        check(f"headers refuse {bad!r}", config_flow.clean_headers(bad) is None)
    check(
        "storage: fine",
        config_flow.clean_local_storage({"ma_access_token": "t", "n": 1}) == {"ma_access_token": "t", "n": "1"},
    )
    check("storage: refuses nesting", config_flow.clean_local_storage({"a": {"b": 1}}) is None)

    typed = {
        "slug": "music",
        "title": " Music ",
        "url": "http://music:8095/",
        "icon": "mdi:music",
        "require_admin": False,
        "persist_login": True,
        "advanced": {"keep_prefix": False, "verify_ssl": True, "local_storage": {"ma_access_token": "t"}},
    }

    async def flow_checks():
        client = FakeClient()
        hass = new_hass(client)

        client.replies.append(FakeUpstream(status=401))
        flow = config_flow.IngressProxyConfigFlow(hass)
        result = await flow.async_step_user(typed)
        check("form: an error page still counts as reachable", result["type"] == "create_entry", result)
        check("form: checked the app's front page", client.requests[-1]["url"] == "http://music:8095/")
        check("form: title tidied", result["title"] == "Music")
        check(
            "form: saved flat",
            result["data"]
            == {
                "title": "Music",
                "icon": "mdi:music",
                "require_admin": False,
                "persist_login": True,
                "keep_prefix": False,
                "verify_ssl": True,
                "slug": "music",
                "url": "http://music:8095",
                "headers": {},
                "local_storage": {"ma_access_token": "t"},
                "username": "",
                "password": "",
                "login_method": "basic",
            },
            result["data"],
        )
        check("form: slug is the unique id", result["unique_id"] == "music")
        check(
            "form: client keeps no cookies and leaves compression alone",
            isinstance(stubs.RECORDED["clients"][0]["cookie_jar"], aiohttp.DummyCookieJar)
            and stubs.RECORDED["clients"][0]["auto_decompress"] is False,
        )

        made = len(stubs.RECORDED["clients"])
        async_get_client(hass, True)
        check("client: reused while open", len(stubs.RECORDED["clients"]) == made)
        client.closed = True
        async_get_client(hass, True)
        client.closed = False
        check("client: replaced once closed", len(stubs.RECORDED["clients"]) == made + 1)

        taken = stubs.ConfigEntry(entry_data(), unique_id="music")
        flow = config_flow.IngressProxyConfigFlow(hass, entries=[taken])
        result = await flow.async_step_user(typed)
        check("form: slug taken", result["type"] == "form" and result["errors"] == {"slug": "slug_taken"}, result)
        check("form: what was typed is kept", result["data_schema"].suggested is typed)

        flow = config_flow.IngressProxyConfigFlow(hass)
        result = await flow.async_step_user({**typed, "slug": "Music", "url": "music:8095", "title": " "})
        check(
            "form: field errors",
            result["errors"] == {"slug": "invalid_slug", "url": "invalid_url", "title": "invalid_title"},
            result["errors"],
        )
        requests_before = len(client.requests)
        result = await flow.async_step_user({**typed, "advanced": {"headers": {"X": "a\nb"}}})
        check("form: bad headers", result["errors"] == {"base": "invalid_headers"}, result["errors"])
        check("form: nothing sent to the app while the form has errors", len(client.requests) == requests_before)

        client.replies.append(aiohttp.ClientError("refused"))
        result = await config_flow.IngressProxyConfigFlow(hass).async_step_user(typed)
        check("form: unreachable", result["errors"] == {"base": "cannot_connect"}, result["errors"])
        client.replies.append(aiohttp.ClientConnectorCertificateError("self-signed"))
        result = await config_flow.IngressProxyConfigFlow(hass).async_step_user(typed)
        check("form: certificate", result["errors"] == {"base": "certificate"}, result["errors"])
        client.replies.append(TimeoutError())
        result = await config_flow.IngressProxyConfigFlow(hass).async_step_user(typed)
        check("form: timeout", result["errors"] == {"base": "cannot_connect"}, result["errors"])

        flow = config_flow.IngressProxyConfigFlow(hass)
        result = await flow.async_step_user()
        schema_keys = list(result["data_schema"].schema)
        check("form: asks for the slug when adding", "slug" in schema_keys and result["data_schema"].suggested is None)

        entry = stubs.ConfigEntry(entry_data(headers={"X-Key": "1"}), unique_id="music", title="Music")
        flow = config_flow.IngressProxyConfigFlow(hass, entries=[entry], reconfigure_entry=entry)
        result = await flow.async_step_reconfigure()
        check("reconfigure: no slug field", "slug" not in list(result["data_schema"].schema))
        suggested = result["data_schema"].suggested
        check(
            "reconfigure: saved settings shown, advanced ones in their section",
            suggested["title"] == "Music"
            and suggested["advanced"]["headers"] == {"X-Key": "1"}
            and "headers" not in suggested,
            suggested,
        )
        client.replies.append(FakeUpstream())
        changed = {key: value for key, value in typed.items() if key != "slug"}
        changed["title"] = "Music Assistant"
        result = await flow.async_step_reconfigure(changed)
        check("reconfigure: saved", result == {"type": "abort", "reason": "reconfigure_successful"}, result)
        check(
            "reconfigure: slug kept, title changed, headers cleared",
            entry.data["slug"] == "music" and entry.title == "Music Assistant" and entry.data["headers"] == {},
            entry.data,
        )

        # ---- the Login section
        with_login = {**typed, "login": {"username": " admin ", "password": "pa:ss é", "login_method": "basic"}}
        client.replies.append(FakeUpstream())
        result = await config_flow.IngressProxyConfigFlow(hass).async_step_user(with_login)
        sent = dict(client.requests[-1]["headers"])
        check("login: basic checked with the login", sent.get("Authorization") == "Basic YWRtaW46cGE6c3Mgw6k=", sent)
        check(
            "login: saved flat, username tidied",
            result["type"] == "create_entry"
            and (result["data"]["username"], result["data"]["password"], result["data"]["login_method"])
            == ("admin", "pa:ss é", "basic"),
            result,
        )
        client.replies.append(FakeUpstream(status=401))
        result = await config_flow.IngressProxyConfigFlow(hass).async_step_user(with_login)
        check("login: basic refused with 401", result["errors"] == {"base": "invalid_auth"}, result["errors"])

        for login, error in (
            ({"username": "admin"}, "missing_password"),
            ({"username": "ad:min", "password": "x"}, "invalid_username"),
        ):
            requests_before = len(client.requests)
            result = await config_flow.IngressProxyConfigFlow(hass).async_step_user({**typed, "login": login})
            check(f"login: {error}", result["errors"] == {"base": error}, result["errors"])
            check(f"login: {error} sends nothing", len(client.requests) == requests_before)
        client.replies.append(FakeUpstream())
        result = await config_flow.IngressProxyConfigFlow(hass).async_step_user(
            {**typed, "login": {"username": " ", "password": "stray", "login_method": "basic"}}
        )
        check(
            "login: no username, no password kept",
            result["data"]["username"] == "" and result["data"]["password"] == "",
            result["data"],
        )
        check("login: no username, no Authorization", "Authorization" not in dict(client.requests[-1]["headers"]))

        esphome_login = {**typed, "login": {"username": "admin", "password": "hunter2", "login_method": "esphome"}}
        client.esphome = FakeEsphome()
        client.replies.append(FakeUpstream())
        result = await config_flow.IngressProxyConfigFlow(hass).async_step_user(esphome_login)
        check("login: esphome logged in to check", result["type"] == "create_entry" and client.esphome.tokens)
        check(
            "login: esphome front page checked without Basic",
            "Authorization" not in dict(client.requests[-1]["headers"]),
        )
        client.esphome = FakeEsphome(password="other")
        client.replies.append(FakeUpstream())
        result = await config_flow.IngressProxyConfigFlow(hass).async_step_user(esphome_login)
        check("login: esphome refused", result["errors"] == {"base": "invalid_auth"}, result["errors"])
        client.esphome = None
        client.replies.append(FakeUpstream())
        result = await config_flow.IngressProxyConfigFlow(hass).async_step_user(esphome_login)
        check("login: not a Device Builder", result["errors"] == {"base": "not_esphome"}, result["errors"])

        saved = stubs.ConfigEntry(
            entry_data(username="admin", password="hunter2", login_method="esphome"), unique_id="music", title="Music"
        )
        flow = config_flow.IngressProxyConfigFlow(hass, entries=[saved], reconfigure_entry=saved)
        result = await flow.async_step_reconfigure()
        suggested = result["data_schema"].suggested
        check(
            "reconfigure: login shown, the password never sent to the browser",
            suggested["login"] == {"username": "admin", "login_method": "esphome"} and "hunter2" not in repr(suggested),
            suggested,
        )
        check("reconfigure: login section open", result["data_schema"].schema["login"].options == {"collapsed": False})
        client.esphome = FakeEsphome()
        client.replies.append(FakeUpstream())
        changed = {key: value for key, value in typed.items() if key != "slug"}
        result = await flow.async_step_reconfigure(
            {**changed, "login": {"username": "admin", "password": "", "login_method": "esphome"}}
        )
        check("reconfigure: a blank password keeps the saved one", saved.data["password"] == "hunter2", saved.data)
        check("reconfigure: and logs in with it", client.esphome.logins == [("admin", "hunter2")])
        client.replies.append(FakeUpstream())
        await flow.async_step_reconfigure({**changed, "login": {"username": "", "login_method": "esphome"}})
        check(
            "reconfigure: clearing the username removes the login",
            saved.data["username"] == "" and saved.data["password"] == "",
            saved.data,
        )
        result = await config_flow.IngressProxyConfigFlow(hass).async_step_user()
        check(
            "form: login section closed when adding",
            result["data_schema"].schema["login"].options == {"collapsed": True},
        )
        login_fields = {str(key): key for key in result["data_schema"].schema["login"].schema.schema}
        shown_with_username = {"field": "username", "operator": "exists"}
        check(
            "form: password and how to log in only shown once a username is typed",
            login_fields["password"].visible == shown_with_username
            and login_fields["login_method"].visible == shown_with_username
            and getattr(login_fields["username"], "visible", None) is None,
        )
        # Home Assistant leaves hidden fields out of what the form sends: no username, so no password or method.
        client.replies.append(FakeUpstream())
        result = await config_flow.IngressProxyConfigFlow(hass).async_step_user(
            {**typed, "slug": "nologin", "login": {}}
        )
        check(
            "form: no username, no login (the hidden fields aren't sent)",
            result["type"] == "create_entry"
            and result["data"]["username"] == ""
            and result["data"]["password"] == ""
            and result["data"]["login_method"] == "basic",
            result,
        )

    asyncio.run(flow_checks())


# ---------------------------------------------------------------- __init__.py and websocket.py
def test_lifecycle_and_command():
    """Apps come and go with their panels; the session command decides who may open them."""

    async def checks():
        client = FakeClient()
        hass = new_hass(client)
        # A cookie file from an earlier version is read as is.
        old = stubs.Store(hass, 1, "ingress_proxy.cookies")
        old.saved = {"music": {"sid": "kept"}}
        original_store = stubs.Store

        class PreloadedStore(original_store):
            def __init__(self, hass, version, key):
                super().__init__(hass, version, key)
                self.saved = old.saved

        ingress_proxy.Store = PreloadedStore
        try:
            await ingress_proxy.async_setup(hass, {})
        finally:
            ingress_proxy.Store = original_store
        data = hass.data["ingress_proxy"]
        check("setup: earlier cookie file read", data.cookie_jar.cookies_for("music") == {"sid": "kept"})
        check(
            "setup: one view",
            len(stubs.RECORDED["views"]) == 1 and isinstance(stubs.RECORDED["views"][0], proxy.IngressProxyView),
        )
        check(
            "setup: panel script folder served without caching",
            [(c.url_path, Path(c.path).name, c.cache_headers) for c in stubs.RECORDED["static_paths"]]
            == [("/ingress_proxy_static", "frontend", False)],
        )
        check("setup: session command registered", stubs.RECORDED["ws_commands"] == [websocket.ws_session])
        check("setup: YAML refused", ingress_proxy.CONFIG_SCHEMA == "config entry only: ingress_proxy")

        version = json.loads((stubs.ROOT / "custom_components/ingress_proxy/manifest.json").read_text())["version"]
        entry = stubs.ConfigEntry(entry_data())
        await ingress_proxy.async_setup_entry(hass, entry)
        panel = stubs.RECORDED["panels"].get("proxy-music")
        check(
            "entry: panel registered",
            panel
            == {
                "frontend_url_path": "proxy-music",
                "webcomponent_name": f"ingress-proxy-panel-{version.replace('.', '-')}",
                "module_url": f"/ingress_proxy_static/panel.js?v={version}",
                "sidebar_title": "Music",
                "sidebar_icon": "mdi:music",
                "require_admin": True,
                "config": {"slug": "music"},
            },
            panel,
        )
        check(
            "entry: app served",
            data.apps["music"] == entry.runtime_data and entry.runtime_data.url == "http://music:8095",
        )

        def ask(slug, is_admin):
            connection = stubs.ActiveConnection(is_admin)
            websocket.ws_session(hass, connection, {"id": 1, "type": "ingress_proxy/session", "slug": slug})
            return connection

        check("command: unknown app", ask("nope", True).errors == ["not_found"])
        check("command: admins-only app refuses a user", ask("music", False).errors == ["unauthorized"])
        answer = ask("music", True).results[0]
        check(
            "command: answer tells the panel how to store it",
            answer["cookie"] == SESSION_COOKIE
            and answer["path"] == "/api/ingress_proxy/music"
            and answer["max_age"] == SESSION_TTL_SECONDS
            and answer["local_storage"] == {"ma_access_token": "secret"},
            answer,
        )
        check("command: token works for the app", data.sessions.find(answer["session"], "music").is_admin)

        await ingress_proxy.async_unload_entry(hass, entry)
        check(
            "unload: panel removed",
            "proxy-music" not in stubs.RECORDED["panels"] and stubs.RECORDED["removed_panels"] == ["proxy-music"],
        )
        check("unload: app no longer served", "music" not in data.apps)
        check("unload: its sessions end", data.sessions.find(answer["session"], "music") is None)

        # Reconfigure reloads the entry: set up again with new settings, and the panel registers cleanly.
        entry = stubs.ConfigEntry(entry_data(require_admin=False, title="Tunes"))
        await ingress_proxy.async_setup_entry(hass, entry)
        check(
            "reload: panel back with the new settings",
            stubs.RECORDED["panels"]["proxy-music"]["sidebar_title"] == "Tunes",
        )
        answer = ask("music", False).results[0]
        check("command: open app lets a user in", data.sessions.find(answer["session"], "music").is_admin is False)

        await ingress_proxy.async_unload_entry(hass, entry)
        await ingress_proxy.async_remove_entry(hass, entry)
        check("remove: its kept cookies deleted", data.cookie_jar.cookies_for("music") == {})

        # Removing an app while the integration isn't running still deletes its cookies.
        hass2 = new_hass(client)
        stubs.RECORDED["stores"].clear()

        class SavedStore(original_store):
            def __init__(self, hass, version, key):
                super().__init__(hass, version, key)
                self.saved = {"music": {"sid": "x"}, "other": {"t": "1"}}

        ingress_proxy.Store = SavedStore
        try:
            await ingress_proxy.async_remove_entry(hass2, stubs.ConfigEntry(entry_data()))
        finally:
            ingress_proxy.Store = original_store
        saved = stubs.RECORDED["stores"]["ingress_proxy.cookies"].saved
        check("remove while stopped: only that app's cookies deleted", saved == {"other": {"t": "1"}}, saved)

    asyncio.run(checks())


# ---------------------------------------------------------------- proxy.py (HTTP, with the fakes)
def test_view():
    """Who gets through, what the app is sent, and what comes back."""

    async def checks():
        client = FakeClient()
        hass = new_hass(client)
        await ingress_proxy.async_setup(hass, {})
        data = hass.data["ingress_proxy"]
        await ingress_proxy.async_setup_entry(hass, stubs.ConfigEntry(entry_data(), entry_id="1"))
        await ingress_proxy.async_setup_entry(
            hass,
            stubs.ConfigEntry(
                entry_data(slug="z2m", title="Zigbee", url="http://z2m:8080", persist_login=False, require_admin=False),
                entry_id="2",
            ),
        )
        view = stubs.RECORDED["views"][0]
        admin = data.sessions.create("music", is_admin=True)
        user = data.sessions.create("music", is_admin=False)  # made while the app was open to users, say
        z2m = data.sessions.create("z2m", is_admin=False)
        data.cookie_jar.update("music", ["sid=kept"])

        response = await view.get(FakeRequest("/api/ingress_proxy/nope/"), "nope", "")
        check("view: unknown app 404", response.status == 404)
        response = await view.get(FakeRequest("/api/ingress_proxy/music/"), "music", "")
        check("view: no session 401", response.status == 401 and "sidebar" in response.text)
        response = await view.get(FakeRequest("/api/ingress_proxy/music/", cookies={SESSION_COOKIE: z2m}), "music", "")
        check("view: another app's session 401", response.status == 401)
        response = await view.get(FakeRequest("/api/ingress_proxy/music/", cookies={SESSION_COOKIE: user}), "music", "")
        check("view: a user's session on an admins-only app 401", response.status == 401)
        stale = f"{SESSION_COOKIE}=left-over-from-an-older-version"
        response = await view.get(FakeRequest("/api/ingress_proxy/music/", cookie_header=stale), "music", "")
        check("view: only a stale session cookie 401", response.status == 401)
        check("view: nothing reached the app", client.requests == [])

        # A browser can hold the session cookie for two paths (this app's, and /api/ingress_proxy from an older
        # version) and sends both, longest path first. aiohttp's request.cookies keeps only the last one.
        for header in (f"{SESSION_COOKIE}={admin}; {stale}", f"{stale}; {SESSION_COOKIE}={admin}"):
            client.replies.append(FakeUpstream())
            response = await view.get(FakeRequest("/api/ingress_proxy/music/", cookie_header=header), "music", "")
            check(f"view: a valid session among stale ones gets in ({header[:32]}...)", response.status == 200)
            if response.status == 200:
                sent = client.requests.pop()["headers"]
                check("view: and no session cookie reaches the app", not any(SESSION_COOKIE in v for _, v in sent))
            else:
                client.replies.clear()

        signed_in = {SESSION_COOKIE: admin, "theme": "dark"}
        response = await view.get(FakeRequest("/api/ingress_proxy/music", query="a=1", cookies=signed_in), "music")
        check(
            "view: bare prefix redirects to the trailing /",
            response.status == 307 and response.headers["Location"] == "/api/ingress_proxy/music/?a=1",
        )
        response = await view.get(
            FakeRequest("/api/ingress_proxy/music/a/%2e%2e/b", cookies=signed_in), "music", "a/../b"
        )
        check("view: parent segment 400", response.status == 400)

        client.replies.append(
            FakeUpstream(
                headers=[
                    ("Content-Type", "text/plain"),
                    ("Set-Cookie", "sid=new; Path=/"),
                    ("X-Frame-Options", "DENY"),
                ],
                chunks=[b"one ", b"two"],
            )
        )
        request = FakeRequest(
            "/api/ingress_proxy/music/a%20b", query="q=1", cookies=signed_in, headers=[("Accept", "*/*")]
        )
        response = await view.get(request, "music", "a b")
        sent = client.requests[-1]
        check("view: app URL", sent["url"] == "http://music:8095/a%20b?q=1", sent["url"])
        check(
            "view: kept cookies added, session cookie never sent",
            ("Cookie", "theme=dark; sid=kept") in sent["headers"],
            sent["headers"],
        )
        check("view: GET sends no body", sent["data"] is None)
        check(
            "view: reply streamed whole",
            response.status == 200 and response.chunks == [b"one ", b"two"] and response.eof,
        )
        check(
            "view: reply headers filtered",
            ("Content-Type", "text/plain") in response.headers
            and not any(n == "X-Frame-Options" for n, _ in response.headers),
        )
        check(
            "view: persist login keeps Set-Cookie away from the browser",
            not any(n == "Set-Cookie" for n, _ in response.headers),
        )
        check("view: and remembers it", data.cookie_jar.cookies_for("music") == {"sid": "new"})

        client.replies.append(FakeUpstream(headers=[("Set-Cookie", "z=1; Domain=z2m; Path=/")]))
        response = await view.post(
            FakeRequest("/api/ingress_proxy/z2m/login", method="POST", cookies={SESSION_COOKIE: z2m}), "z2m", "login"
        )
        check("view: POST streams the body", client.requests[-1]["data"] == "request body stream")
        check(
            "view: without persist login the browser gets the cookie, rewritten",
            ("Set-Cookie", "z=1; Path=/api/ingress_proxy/z2m") in response.headers,
            response.headers,
        )
        check("view: and Home Assistant keeps none", data.cookie_jar.cookies_for("z2m") == {})

        client.replies.append(aiohttp.ClientError("refused"))
        response = await view.get(FakeRequest("/api/ingress_proxy/music/", cookies=signed_in), "music", "")
        check("view: app down 502", response.status == 502 and "Music" in response.text)

        upstream = FakeUpstream(chunks=[b"a", b"b", b"c"], fail_after=1)
        client.replies.append(upstream)
        request = FakeRequest("/api/ingress_proxy/music/big", cookies=signed_in)
        response = await view.get(request, "music", "big")
        check(
            "view: app breaks off: connection cut, reply not finished",
            request.transport.closed and not response.eof and response.chunks == [b"a"],
        )
        check("view: app's reply released", upstream.released)

        upstream = FakeUpstream(chunks=[b"a", b"b", b"c"])
        client.replies.append(upstream)
        request = FakeRequest("/api/ingress_proxy/music/big", cookies=signed_in, browser_leaves_after=1)
        response = await view.get(request, "music", "big")
        check(
            "view: browser leaves: stops quietly", response.chunks == [b"a"] and not response.eof and upstream.released
        )

        # ---- an app Home Assistant logs in to
        basic = stubs.ConfigEntry(
            entry_data(slug="esp", url="http://esphome:6052", username="admin", password="hunter2"), entry_id="3"
        )
        await ingress_proxy.async_setup_entry(hass, basic)
        esp = {SESSION_COOKIE: data.sessions.create("esp", is_admin=True)}
        browser_login = [("Authorization", "Basic YnJvd3Nlcjp4")]
        client.replies.append(FakeUpstream(status=401, headers=[("WWW-Authenticate", 'Basic realm="ESPHome"')]))
        response = await view.get(FakeRequest("/api/ingress_proxy/esp/", cookies=esp, headers=browser_login), "esp")
        sent = [value for name, value in client.requests[-1]["headers"] if name.lower() == "authorization"]
        check("view: basic login sent, the browser's replaced", sent == ["Basic YWRtaW46aHVudGVyMg=="], sent)
        check(
            "view: no login box for the browser",
            response.status == 401 and not any(n.lower() == "www-authenticate" for n, _ in response.headers),
        )

        client.esphome = FakeEsphome()
        builder = stubs.ConfigEntry(
            entry_data(
                slug="esp", url="http://esphome:6052", username="admin", password="hunter2", login_method="esphome"
            ),
            entry_id="3",
        )
        await ingress_proxy.async_unload_entry(hass, basic)  # what Reconfigure does: reload with the new settings
        await ingress_proxy.async_setup_entry(hass, builder)
        esp = {SESSION_COOKIE: data.sessions.create("esp", is_admin=True)}
        for _ in range(2):
            client.replies.append(FakeUpstream())
            await view.get(FakeRequest("/api/ingress_proxy/esp/devices", cookies=esp), "esp")
        sent = [value for name, value in client.requests[-1]["headers"] if name.lower() == "authorization"]
        check("view: esphome token sent", sent == ["Bearer token1"], sent)
        check("view: esphome logged in once", len(client.esphome.logins) == 1)
        client.replies.append(FakeUpstream(status=401))
        await view.get(FakeRequest("/api/ingress_proxy/esp/devices", cookies=esp), "esp")
        client.replies.append(FakeUpstream())
        await view.get(FakeRequest("/api/ingress_proxy/esp/devices", cookies=esp), "esp")
        sent = [value for name, value in client.requests[-1]["headers"] if name.lower() == "authorization"]
        check("view: esphome token refused, logged in again", sent == ["Bearer token2"], sent)

        client.esphome = FakeEsphome(password="changed")
        data.logins.forget("esp")
        requests_before = len(client.requests)
        response = await view.get(FakeRequest("/api/ingress_proxy/esp/", cookies=esp), "esp")
        check(
            "view: login refused: 502 that says so, nothing sent to the app",
            response.status == 502 and "log in" in response.text and len(client.requests) == requests_before,
        )
        check("view: and the password isn't in it", "hunter2" not in response.text and "changed" not in response.text)

    asyncio.run(checks())


# ---------------------------------------------------------------- the files around the code
def test_files():
    """The manifest, translations and panel script agree with the code."""
    base = stubs.ROOT / "custom_components/ingress_proxy"
    manifest = json.loads((base / "manifest.json").read_text())
    check("manifest: config flow, no requirements", manifest["config_flow"] is True and manifest["requirements"] == [])
    check(
        "manifest: depends on what it imports",
        set(manifest["dependencies"]) == {"frontend", "http", "panel_custom", "websocket_api"},
    )
    strings = json.loads((base / "translations/en.json").read_text())["config"]
    used_errors = {
        "invalid_auth",
        "invalid_username",
        "missing_password",
        "not_esphome",
        "invalid_slug",
        "slug_taken",
        "invalid_title",
        "invalid_url",
        "invalid_headers",
        "invalid_local_storage",
        "cannot_connect",
        "certificate",
    }
    check(
        "translations: every error has words", used_errors <= set(strings["error"]), used_errors - set(strings["error"])
    )
    for step in ("user", "reconfigure"):
        sections = strings["step"][step]["sections"]
        fields = set(strings["step"][step]["data"]) | set(sections["advanced"]["data"]) | set(sections["login"]["data"])
        expected = {
            "title",
            "url",
            "icon",
            "require_admin",
            "persist_login",
            "keep_prefix",
            "verify_ssl",
            "headers",
            "local_storage",
            "username",
            "password",
            "login_method",
        }
        if step == "user":
            expected.add("slug")
        check(f"translations: {step} labels every field", fields == expected, fields ^ expected)
    methods = json.loads((base / "translations/en.json").read_text())["selector"]["login_method"]["options"]
    check("translations: every login method has words", set(methods) == {"basic", "esphome"}, methods)
    script = (base / "frontend/panel.js").read_text()
    check("panel: asks for the same command", '"ingress_proxy/session"' in script)
    check("panel: deletes the older version's cookie", "Path=/api/ingress_proxy; Max-Age=0" in script)
    check(
        "panel: element named with the version, as __init__.py registers it", "ingress-proxy-panel-${VERSION" in script
    )
    for name in ("brand/icon.png", "brand/icon@2x.png"):
        check(f"brand: {name} exists", (base / name).is_file())


if __name__ == "__main__":
    test_rewrite()
    test_cookies()
    test_sessions()
    test_logins()
    test_config_flow()
    test_lifecycle_and_command()
    test_view()
    test_files()
    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED: " + ", ".join(FAILURES))
        sys.exit(1)
    print("All checks passed.")
