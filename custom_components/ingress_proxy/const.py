"""Names and settings shared across the Ingress Proxy integration."""

from __future__ import annotations

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

DEFAULT_ICON = "mdi:application"

# Every app is served under PROXY_BASE/<slug>/, on Home Assistant's own address.
PROXY_BASE = "/api/ingress_proxy"

# The app's sidebar panel lives at /proxy-<slug>.
PANEL_URL_PREFIX = "proxy-"

# Where the panel script is served from. Its file name never changes; the version in the query string and in the
# element's name makes browsers load the new script after an update.
STATIC_URL = "/ingress_proxy_static"
PANEL_SCRIPT = "panel.js"

# The panel asks for a session over Home Assistant's websocket, then keeps it in this cookie, which the browser
# sends with every request the app's iframe makes. Iframes can't send Home Assistant's bearer token, so this is how
# the proxy knows the request comes from someone logged in to Home Assistant.
SESSION_COOKIE = "ingress_proxy_session"
SESSION_TTL_SECONDS = 3600  # the panel renews it every 20 minutes, so it only runs out once the panel is closed

# Upstream cookies kept server-side (see cookies.py), saved in .storage/ingress_proxy.cookies.
COOKIE_STORE_KEY = f"{DOMAIN}.cookies"
COOKIE_STORE_VERSION = 1
COOKIE_SAVE_DELAY_SECONDS = 5  # a login sets several cookies at once; save them in one write

# Large enough to keep streaming fast, small enough not to hold big downloads in memory.
STREAM_CHUNK_BYTES = 64 * 1024

# How long to wait for the app to accept a connection. There is deliberately no total timeout: apps stream
# (live logs, server-sent events, large downloads), and those must not be cut off.
CONNECT_TIMEOUT_SECONDS = 10

# How long the setup form waits for the app before saying it can't be reached.
CHECK_TIMEOUT_SECONDS = 10
