"""The rules for passing requests and responses between the browser and an app.

Kept apart from the proxy view and free of aiohttp, so every rule can be read in one place and tested on its own.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from urllib.parse import unquote, urlsplit

from .models import ProxiedApp

# Headers that only describe one hop of the connection, never passed on (RFC 9110, section 7.6.1).
HOP_BY_HOP = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "proxy-connection",
        "te",
        "trailer",
        "trailers",
        "transfer-encoding",
        "upgrade",
    }
)

# Request headers the proxy doesn't pass to the app as the browser sent them:
#   host                  the app gets its own address (the client sets it from the URL)
#   cookie                rebuilt without the proxy's session cookie, plus the cookies kept for the app
#   origin, referer       they name Home Assistant's address, and apps that check them would refuse the request
#   x-forwarded-*, forwarded, x-ingress-path
#                         set by the proxy itself, so a browser can't pretend to come from somewhere else
REQUEST_HEADERS_TO_DROP = HOP_BY_HOP | {
    "host",
    "cookie",
    "origin",
    "referer",
    "forwarded",
    "x-forwarded-for",
    "x-forwarded-host",
    "x-forwarded-proto",
    "x-forwarded-prefix",
    "x-ingress-path",
}

# Response headers the browser doesn't get from the app. The app runs on Home Assistant's address, so these would
# act on Home Assistant itself:
#   set-cookie                  handled by cookies.py
#   x-frame-options             would stop the app showing in its panel's iframe
#   strict-transport-security   would force https on Home Assistant's whole address
#   clear-site-data             an app's logout could wipe Home Assistant's own login from the browser
#   service-worker-allowed      would let the app's service worker take over pages outside its own path
RESPONSE_HEADERS_TO_DROP = HOP_BY_HOP | {
    "set-cookie",
    "x-frame-options",
    "strict-transport-security",
    "clear-site-data",
    "service-worker-allowed",
}

# The close codes a websocket endpoint may send (RFC 6455, section 7.4). 1005 and 1006 only describe what happened
# and must never be sent.
_SENDABLE_CLOSE_CODES = (range(1000, 1004), range(1007, 1015), range(3000, 5000))
NORMAL_CLOSE = 1000


def has_parent_segment(raw_path: str) -> bool:
    """Whether a path tries to climb out of the app with a ".." segment, written plainly or percent-encoded."""
    return ".." in unquote(raw_path).split("/")


def upstream_url(app: ProxiedApp, raw_tail: str, raw_query: str) -> str:
    """The app's URL for a request, from the path after the app's prefix and the query string.

    Both are used exactly as the browser sent them, still percent-encoded, so the app sees the same URL it would
    see without the proxy.
    """
    if app.keep_prefix:
        path = app.prefix + raw_tail
    else:
        path = raw_tail or "/"
    url = app.url + path
    if raw_query:
        url += "?" + raw_query
    return url


def request_headers(
    incoming: Iterable[tuple[str, str]],
    app: ProxiedApp,
    cookies: Mapping[str, str],
    forwarded_host: str,
    forwarded_proto: str,
    forwarded_for: str | None,
) -> list[tuple[str, str]]:
    """The headers to send to the app.

    `incoming` is the browser's headers as pairs (a header can appear more than once), `cookies` the cookies to
    send, already merged. The app's own headers from its settings come last and replace any the browser sent with
    the same name.
    """
    replaced = {name.lower() for name in app.headers}
    headers = []
    for name, value in incoming:
        lower = name.lower()
        if lower in REQUEST_HEADERS_TO_DROP or lower in replaced:
            continue
        headers.append((name, value))

    # How the app can tell where it is: X-Ingress-Path is what Home Assistant's add-on ingress sends, and
    # X-Forwarded-Prefix the more common name for the same thing.
    headers.append(("X-Ingress-Path", app.prefix))
    headers.append(("X-Forwarded-Prefix", app.prefix))
    headers.append(("X-Forwarded-Host", forwarded_host))
    headers.append(("X-Forwarded-Proto", forwarded_proto))
    if forwarded_for:
        headers.append(("X-Forwarded-For", forwarded_for))
    if cookies:
        headers.append(("Cookie", "; ".join(f"{name}={value}" for name, value in cookies.items())))
    headers.extend(app.headers.items())
    return headers


def response_headers(incoming: Iterable[tuple[str, str]], app: ProxiedApp) -> list[tuple[str, str]]:
    """The app's response headers to pass to the browser, with Location and Content-Security-Policy adjusted."""
    headers = []
    for name, value in incoming:
        lower = name.lower()
        if lower in RESPONSE_HEADERS_TO_DROP:
            continue
        if lower == "location":
            value = rewrite_location(value, app)
        elif lower == "content-security-policy":
            value = strip_frame_ancestors(value)
            if not value:
                continue
        headers.append((name, value))
    return headers


def rewrite_location(location: str, app: ProxiedApp) -> str:
    """Point a redirect that leads back into the app at the app's path on Home Assistant's address.

    A redirect anywhere else (another site, a relative path like "next") is left alone.
    """
    if location.startswith(app.url + "/") or location == app.url:
        # A full URL to the app's internal address, which the browser can't reach: keep only the path.
        location = location[len(app.url) :] or "/"
    elif location.startswith("/") and not location.startswith("//"):
        # A path from the app's root. If the app's URL has a path of its own, the app may include it.
        app_path = urlsplit(app.url).path
        if app_path and (location == app_path or location.startswith(app_path + "/")):
            location = location[len(app_path) :] or "/"
    else:
        return location
    already_prefixed = location == app.prefix or location.startswith(app.prefix + "/")
    if app.keep_prefix or already_prefixed:
        # The app builds its URLs with the prefix itself (keep prefix on, or it reads X-Ingress-Path).
        return location
    return app.prefix + location


def strip_frame_ancestors(policy: str) -> str:
    """Remove frame-ancestors from a Content-Security-Policy, which would stop the app showing in its panel."""
    directives = [directive.strip() for directive in policy.split(";")]
    kept = [directive for directive in directives if directive and not directive.lower().startswith("frame-ancestors")]
    return "; ".join(kept)


def websocket_url(http_url: str) -> str:
    """The ws:// or wss:// address for an http:// or https:// one."""
    if http_url.startswith("https://"):
        return "wss://" + http_url[len("https://") :]
    if http_url.startswith("http://"):
        return "ws://" + http_url[len("http://") :]
    raise ValueError(f"not an http(s) URL: {http_url}")


def close_code_to_send(code: int | None) -> int:
    """The close code to pass on to the other side: the one received, if it's one that may be sent."""
    if code is not None and any(code in allowed for allowed in _SENDABLE_CLOSE_CODES):
        return code
    return NORMAL_CLOSE
