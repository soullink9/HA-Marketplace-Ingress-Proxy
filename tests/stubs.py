"""Stand-ins for Home Assistant (and, when they aren't installed, aiohttp and probatio), so the integration runs
with plain Python.

Importing this module registers fake modules under the real names, then puts custom_components/ on the path so
`import ingress_proxy` loads the real integration. The stand-ins record what the integration asks Home Assistant
to do in RECORDED: panels registered and removed, views, static paths, websocket commands and stored data.

aiohttp and probatio are replaced too, unless the test imported the real ones before this module (test_live_proxy.py
does, for aiohttp): test_integration.py always runs against the stand-ins, so it gives the same result everywhere.
"""

from enum import Enum, StrEnum
from pathlib import Path
import re
import sys
import types

sys.dont_write_bytecode = True  # keep __pycache__ out of the repository

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "custom_components"))

RECORDED = {
    "panels": {},  # frontend_url_path -> the keyword arguments it was registered with
    "removed_panels": [],
    "views": [],
    "static_paths": [],
    "ws_commands": [],
    "stores": {},  # storage key -> the stand-in Store
    "clients": [],  # the keyword arguments each client session was made with
}
REAL_AIOHTTP = "aiohttp" in sys.modules


def _module(name, **attributes):
    module = types.ModuleType(name)
    module.__dict__.update(attributes)
    sys.modules[name] = module
    return module


# ---------- probatio, Home Assistant's validation library (only the parts the integration builds its forms and commands with) ----------
if "probatio" not in sys.modules:

    class Invalid(Exception):
        pass

    class _Marker(str):
        def __new__(cls, schema, default=None):
            marker = super().__new__(cls, schema)
            marker.schema = schema
            marker.default = default
            return marker

    class Required(_Marker):
        pass

    class Optional(_Marker):
        pass

    class Schema:
        def __init__(self, schema, extra=None):
            self.schema = schema

    _module("probatio", Invalid=Invalid, Required=Required, Optional=Optional, Schema=Schema)

import probatio  # noqa: E402  (the real one, or the stand-in above)

# ---------- aiohttp (when it isn't installed) ----------
if not REAL_AIOHTTP:

    class ClientError(Exception):
        pass

    class ClientConnectorError(ClientError, OSError):
        pass

    class ClientConnectorCertificateError(ClientConnectorError):
        pass

    class ClientTimeout:
        def __init__(self, total=None, sock_connect=None, sock_read=None):
            self.total = total
            self.sock_connect = sock_connect
            self.sock_read = sock_read

    class DummyCookieJar:
        pass

    class WSServerHandshakeError(ClientError):
        def __init__(self, status=400):
            super().__init__(f"handshake refused with {status}")
            self.status = status

    class WSMsgType(Enum):
        TEXT = 1
        BINARY = 2
        CLOSE = 8
        ERROR = 258

    class Response:
        def __init__(self, status=200, text=None, headers=None):
            self.status = status
            self.text = text
            self.headers = dict(headers or {})

    class StreamResponse:
        """Records what is sent, as the browser would see it."""

        def __init__(self, status=200, reason=None, headers=None):
            self.status = status
            self.reason = reason
            self.headers = list(headers or [])
            self.request = None
            self.chunks = []
            self.eof = False

        async def prepare(self, request):
            self.request = request

        async def write(self, data):
            # A test request can say the browser goes away after so many chunks.
            limit = getattr(self.request, "browser_leaves_after", None)
            if limit is not None and len(self.chunks) >= limit:
                raise ConnectionResetError("the browser went away")
            self.chunks.append(data)

        async def write_eof(self):
            self.eof = True

    class WebSocketResponse:
        def __init__(self, protocols=(), max_msg_size=4 * 1024 * 1024, heartbeat=None):
            self.protocols = protocols

    web = _module(
        "aiohttp.web",
        Response=Response,
        StreamResponse=StreamResponse,
        WebSocketResponse=WebSocketResponse,
        Request=object,
    )
    _module(
        "aiohttp",
        ClientError=ClientError,
        ClientConnectorError=ClientConnectorError,
        ClientConnectorCertificateError=ClientConnectorCertificateError,
        ClientTimeout=ClientTimeout,
        DummyCookieJar=DummyCookieJar,
        ClientSession=object,
        ClientWebSocketResponse=object,
        WSMsgType=WSMsgType,
        WSServerHandshakeError=WSServerHandshakeError,
        web=web,
    )


# ---------- Home Assistant ----------
class HomeAssistant:
    """Only what the integration touches: hass.data, hass.http, hass.auth, hass.bus, and hass.loop's address lookup."""

    def __init__(self):
        self.data = {}
        self.http = _Http()
        self.auth = _Auth()
        self.bus = _Bus()
        self.loop = _Loop()


class User:
    """A Home Assistant user. As in Home Assistant, a deactivated user is no administrator."""

    def __init__(self, user_id, admin=False, is_active=True):
        self.id = user_id
        self.admin = admin
        self.is_active = is_active

    @property
    def is_admin(self):
        return self.is_active and self.admin


class _Auth:
    """The users Home Assistant knows, by id. Tests add them with add_user."""

    def __init__(self):
        self.users = {}

    def add_user(self, user_id, admin=False):
        self.users[user_id] = User(user_id, admin)
        return self.users[user_id]

    async def async_get_user(self, user_id):
        return self.users.get(user_id)


class Event:
    def __init__(self, event_type, data):
        self.event_type = event_type
        self.data = data


class _Bus:
    """Collects listeners; async_fire calls them at once."""

    def __init__(self):
        self.listeners = {}

    def async_listen(self, event_type, listener):
        self.listeners.setdefault(event_type, []).append(listener)
        return lambda: self.listeners[event_type].remove(listener)

    def async_fire(self, event_type, data):
        for listener in self.listeners.get(event_type, []):
            listener(Event(event_type, data))


class _Loop:
    """Address lookups answered from `addresses` (name -> list of IPs); IP addresses answer themselves."""

    def __init__(self):
        self.addresses = {}

    async def getaddrinfo(self, host, port, proto=0):
        import ipaddress
        import socket

        try:
            ipaddress.ip_address(host)
            found = [host]
        except ValueError:
            found = self.addresses.get(host)
        if found is None:
            raise socket.gaierror(f"no address for {host}")
        return [(socket.AF_INET, socket.SOCK_STREAM, proto, "", (address, 0)) for address in found]


class _Http:
    def register_view(self, view):
        RECORDED["views"].append(view)

    async def async_register_static_paths(self, configs):
        RECORDED["static_paths"].extend(configs)


class HomeAssistantView:
    pass


class StaticPathConfig:
    def __init__(self, url_path, path, cache_headers=True):
        self.url_path = url_path
        self.path = path
        self.cache_headers = cache_headers


def callback(func):
    return func


class ConfigEntryError(Exception):
    pass


_module("homeassistant")
_module("homeassistant.const", CONF_URL="url", CONF_USERNAME="username", CONF_PASSWORD="password")
_module("homeassistant.core", HomeAssistant=HomeAssistant, Event=Event, callback=callback)
_module("homeassistant.auth", EVENT_USER_REMOVED="user_removed")
_module("homeassistant.exceptions", ConfigEntryError=ConfigEntryError)
_module("homeassistant.components")
_module("homeassistant.components.http", HomeAssistantView=HomeAssistantView, StaticPathConfig=StaticPathConfig)


async def async_register_panel(hass, **kwargs):
    path = kwargs["frontend_url_path"]
    if path in RECORDED["panels"]:
        raise ValueError(f"Overwriting panel {path}")  # what Home Assistant does
    RECORDED["panels"][path] = kwargs


def async_remove_panel(hass, frontend_url_path, *, warn_if_unknown=True):
    RECORDED["panels"].pop(frontend_url_path, None)
    RECORDED["removed_panels"].append(frontend_url_path)


panel_custom = _module("homeassistant.components.panel_custom", async_register_panel=async_register_panel)
frontend = _module("homeassistant.components.frontend", async_remove_panel=async_remove_panel)


def websocket_command(schema):
    def decorate(func):
        func.command = schema["type"]
        func.schema = schema
        return func

    return decorate


def async_register_command(hass, handler):
    RECORDED["ws_commands"].append(handler)


class ActiveConnection:
    """Collects the answers a websocket command sends."""

    def __init__(self, is_admin, user_id=None):
        self.user = User(user_id or ("admin" if is_admin else "user"), admin=is_admin)
        self.results = []
        self.errors = []

    def send_result(self, msg_id, result):
        self.results.append(result)

    def send_error(self, msg_id, code, message):
        self.errors.append(code)


websocket_api = _module(
    "homeassistant.components.websocket_api",
    websocket_command=websocket_command,
    async_register_command=async_register_command,
    ActiveConnection=ActiveConnection,
    ERR_NOT_FOUND="not_found",
    ERR_UNAUTHORIZED="unauthorized",
)
sys.modules["homeassistant.components"].frontend = frontend
sys.modules["homeassistant.components"].panel_custom = panel_custom
sys.modules["homeassistant.components"].websocket_api = websocket_api


class ConfigEntry[T]:
    def __init__(self, data, entry_id="entry1", unique_id=None, title=""):
        self.data = data
        self.entry_id = entry_id
        self.unique_id = unique_id
        self.title = title
        self.runtime_data = None


class ConfigFlow:
    """Enough of Home Assistant's ConfigFlow for the form logic: results come back as plain dicts."""

    def __init_subclass__(cls, domain=None, **kwargs):
        super().__init_subclass__(**kwargs)

    def __init__(self, hass, entries=(), reconfigure_entry=None):
        self.hass = hass
        self._entries = list(entries)
        self._reconfigure_entry = reconfigure_entry
        self.unique_id = None

    def _async_current_ids(self, include_ignore=True):
        return {entry.unique_id for entry in self._entries}

    def _get_reconfigure_entry(self):
        return self._reconfigure_entry

    async def async_set_unique_id(self, unique_id):
        self.unique_id = unique_id

    def async_create_entry(self, title, data):
        return {"type": "create_entry", "title": title, "data": data, "unique_id": self.unique_id}

    def async_update_reload_and_abort(self, entry, title, data):
        entry.title = title
        entry.data = data
        return {"type": "abort", "reason": "reconfigure_successful"}

    def async_show_form(self, step_id, data_schema, errors):
        return {"type": "form", "step_id": step_id, "data_schema": data_schema, "errors": errors}

    def add_suggested_values_to_schema(self, schema, suggested):
        schema.suggested = suggested
        return schema


_module("homeassistant.config_entries", ConfigEntry=ConfigEntry, ConfigFlow=ConfigFlow, ConfigFlowResult=dict)


class section:  # noqa: N801  (Home Assistant's name)
    def __init__(self, schema, options=None):
        self.schema = schema
        self.options = options


class FlowRequired(probatio.Required):
    """Home Assistant's own marker: a probatio one that can be shown only while other fields match `visible`."""

    def __new__(cls, schema, default=None, visible=None):
        marker = super().__new__(cls, schema, default)
        marker.visible = visible
        return marker


class FlowOptional(probatio.Optional):
    def __new__(cls, schema, default=None, visible=None):
        marker = super().__new__(cls, schema, default)
        marker.visible = visible
        return marker


_module("homeassistant.data_entry_flow", section=section, Required=FlowRequired, Optional=FlowOptional)
sys.modules["homeassistant"].data_entry_flow = sys.modules["homeassistant.data_entry_flow"]


class ConditionOperator(StrEnum):
    EQ = "eq"
    EXISTS = "exists"


_module("homeassistant.helpers.field_visibility", ConditionOperator=ConditionOperator, FieldCondition=dict)


def slug(value):
    """Home Assistant's rule: the value must already be what slugify would make of it."""
    if not isinstance(value, str) or not re.fullmatch(r"[a-z0-9]+(_[a-z0-9]+)*", value):
        raise probatio.Invalid(f"invalid slug {value}")
    return value


_module("homeassistant.helpers")
_module(
    "homeassistant.helpers.config_validation",
    slug=slug,
    config_entry_only_config_schema=lambda domain: f"config entry only: {domain}",
)
sys.modules["homeassistant.helpers"].config_validation = sys.modules["homeassistant.helpers.config_validation"]


class _Selector:
    def __init__(self, config=None):
        self.config = config


class TextSelectorType(StrEnum):
    URL = "url"
    PASSWORD = "password"


class SelectSelectorMode(StrEnum):
    DROPDOWN = "dropdown"


_module(
    "homeassistant.helpers.selector",
    IconSelector=_Selector,
    ObjectSelector=_Selector,
    TextSelector=_Selector,
    TextSelectorConfig=dict,
    TextSelectorType=TextSelectorType,
    SelectSelector=_Selector,
    SelectSelectorConfig=dict,
    SelectSelectorMode=SelectSelectorMode,
)


class Store:
    """Keeps saved data in memory; async_delay_save writes at once, so the tests can read it straight away."""

    def __init__(self, hass, version, key):
        self.key = key
        self.saved = None
        self.saves = 0
        self.removed = False
        RECORDED["stores"][key] = self

    async def async_remove(self):
        self.saved = None
        self.removed = True

    async def async_load(self):
        return self.saved

    def async_delay_save(self, data_func, delay=0):
        self.saved = _deep_copy(data_func())
        self.saves += 1


def _deep_copy(value):
    if isinstance(value, dict):
        return {key: _deep_copy(item) for key, item in value.items()}
    return value


_module("homeassistant.helpers.storage", Store=Store)
_module("homeassistant.helpers.typing", ConfigType=dict)
_module("homeassistant.util")


class HassKey[T](str):
    pass


_module("homeassistant.util.hass_dict", HassKey=HassKey)


def async_create_clientsession(hass, verify_ssl=True, **kwargs):
    RECORDED["clients"].append({"verify_ssl": verify_ssl, **kwargs})
    factory = hass.data.get("test_client_factory")
    if factory is None:
        raise RuntimeError("the test didn't say which client to use")
    return factory(verify_ssl, **kwargs)


_module("homeassistant.helpers.aiohttp_client", async_create_clientsession=async_create_clientsession)


async def async_get_integration(hass, domain):
    import json

    manifest = json.loads((ROOT / "custom_components" / domain / "manifest.json").read_text())
    return types.SimpleNamespace(version=manifest["version"])


_module("homeassistant.loader", async_get_integration=async_get_integration)
