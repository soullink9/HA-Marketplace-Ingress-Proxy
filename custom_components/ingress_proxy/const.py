"""Names and settings shared across the Ingress Proxy integration."""

# Integration domain. Must match the folder name and manifest.json.
DOMAIN = "ingress_proxy"

# What each config entry (one proxied app) stores. CONF_URL comes from homeassistant.const.
CONF_SLUG = "slug"
CONF_TITLE = "title"
CONF_ICON = "icon"
CONF_KEEP_PREFIX = "keep_prefix"
CONF_PERSIST_LOGIN = "persist_login"
CONF_REQUIRE_ADMIN = "require_admin"
CONF_VERIFY_SSL = "verify_ssl"
CONF_HEADERS = "headers"
CONF_LOCAL_STORAGE = "local_storage"
CONF_LOGIN_METHOD = "login_method"  # with CONF_USERNAME and CONF_PASSWORD from homeassistant.const

# How the proxy logs in to an app that has a username (see logins.py).
LOGIN_BASIC = "basic"  # HTTP Basic on every request: most apps, and the older ESPHome dashboard
LOGIN_ESPHOME = "esphome"  # ESPHome Device Builder: log in once over its websocket, then send the token it gives
LOGIN_METHODS = (LOGIN_BASIC, LOGIN_ESPHOME)

DEFAULT_ICON = "mdi:application"

# Every app is served under PROXY_BASE/<slug>/, on Home Assistant's own address.
PROXY_BASE = "/api/ingress_proxy"

# The app's sidebar panel lives at /proxy-<slug>.
PANEL_URL_PREFIX = "proxy-"

# Where the panel script is served from. Its file name never changes; the version in the query string and in the
# element's name makes browsers load the new script after an update.
STATIC_URL = "/ingress_proxy_static"
PANEL_SCRIPT = "panel.js"

# The panel asks for a session over Home Assistant's websocket, then keeps it in a cookie, which the browser sends
# with every request the app's iframe makes. Iframes can't send Home Assistant's bearer token, so this is how the
# proxy knows the request comes from someone logged in to Home Assistant. Each app has a cookie of its own,
# SESSION_COOKIE_PREFIX + slug (see session_cookie_name), and no cookie starting with the prefix ever reaches an app
# or comes from one.
SESSION_COOKIE_PREFIX = "ingress_proxy_session"
SESSION_TTL_SECONDS = 3600  # the panel renews it every 20 minutes, so it only runs out once the panel is closed

# Upstream cookies kept server-side (see cookies.py), by app and Home Assistant user, saved in
# .storage/ingress_proxy.user_cookies. Before 1.5.0 they were shared by every user, in .storage/ingress_proxy.cookies;
# that file is deleted at start-up, since nobody can tell whose login it holds.
COOKIE_STORE_KEY = f"{DOMAIN}.user_cookies"
COOKIE_STORE_VERSION = 1
SHARED_COOKIE_STORE_KEY = f"{DOMAIN}.cookies"
COOKIE_SAVE_DELAY_SECONDS = 5  # a login sets several cookies at once; save them in one write

# Large enough to keep streaming fast, small enough not to hold big downloads in memory.
STREAM_CHUNK_BYTES = 64 * 1024

# How long an HTTP reply from the app may go without sending anything before the proxy gives up on it, so an app
# that hangs doesn't hold the connection forever. Long enough for long polling and server-sent events, which send
# something at least every few minutes.
READ_IDLE_TIMEOUT_SECONDS = 300

# The largest websocket message relayed either way. Big enough for any app's real messages (aiohttp's own default
# is 4 MiB), small enough that one message can't fill Home Assistant's memory.
WEBSOCKET_MAX_MESSAGE_BYTES = 16 * 1024 * 1024

# Both ends of a relayed websocket are pinged this often, and closed when a ping goes unanswered, so a browser or app
# that vanished without closing doesn't keep the relay open.
WEBSOCKET_HEARTBEAT_SECONDS = 30

# The most requests (HTTP replies being streamed) and websockets one app can have open through the proxy at once.
# Past that the proxy answers 503, so one app, or one runaway page, can't use up Home Assistant's connections.
MAX_REQUESTS_PER_APP = 100
MAX_WEBSOCKETS_PER_APP = 25

# How long to wait for the app to accept a connection. There is deliberately no total timeout: apps stream
# (live logs, server-sent events, large downloads), and those must not be cut off.
CONNECT_TIMEOUT_SECONDS = 10

# How long the setup form waits for the app before saying it can't be reached. Logging in to an app (logins.py)
# gets the same time.
CHECK_TIMEOUT_SECONDS = 10

# How long a request waits for another request's login to the app to finish before giving up with a 502.
LOGIN_WAIT_SECONDS = 2 * CHECK_TIMEOUT_SECONDS

# Cloud instance metadata services. Home Assistant in a cloud VM can reach them, and they hand out the VM's
# credentials, so no app may be set up at these addresses. Every other address is allowed: proxying apps on your
# own network, or on Home Assistant's own host, is what this integration is for.
METADATA_ADDRESSES = frozenset({"169.254.169.254", "fd00:ec2::254"})

# Browser storage keys Home Assistant's own frontend uses. An app's storage entries can't set them: the panel would
# overwrite Home Assistant's login (hassTokens) or settings in the browser.
RESERVED_LOCAL_STORAGE_KEYS = frozenset(
    {
        "hassTokens",
        "selectedLanguage",
        "selectedTheme",
        "dockedSidebar",
        "sidebarPanelOrder",
        "sidebarHiddenPanels",
        "defaultPanel",
        "enableShortcuts",
        "suspendWhenHidden",
        "vibrate",
    }
)


def session_cookie_name(slug: str) -> str:
    """The name of the cookie that carries an app's panel session."""
    return f"{SESSION_COOKIE_PREFIX}_{slug}"
