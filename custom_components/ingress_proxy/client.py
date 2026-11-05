"""The HTTP client the proxy talks to the apps with."""

from __future__ import annotations

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .const import CONNECT_TIMEOUT_SECONDS, DOMAIN

_CLIENTS_KEY = f"{DOMAIN}_clients"


def async_get_client(hass: HomeAssistant, verify_ssl: bool) -> aiohttp.ClientSession:
    """Return the proxy's own client session, one for checked and one for unchecked certificates.

    Home Assistant's shared session isn't used, because the proxy needs a client that:
      - keeps no cookies of its own (DummyCookieJar), so one user's login to an app can't leak into another's
        requests; the cookies sent are exactly the ones cookies.py chose
      - leaves compressed replies compressed (auto_decompress=False), so they pass to the browser byte for byte
        with their Content-Encoding and Content-Length intact
      - has no total timeout, so live streams and big downloads aren't cut off; only connecting is limited

    Home Assistant closes these sessions when it stops. A closed one is replaced, so a request never fails on it.
    """
    clients: dict[bool, aiohttp.ClientSession] = hass.data.setdefault(_CLIENTS_KEY, {})
    if verify_ssl not in clients or clients[verify_ssl].closed:
        clients[verify_ssl] = async_create_clientsession(
            hass,
            verify_ssl=verify_ssl,
            cookie_jar=aiohttp.DummyCookieJar(),
            auto_decompress=False,
            timeout=aiohttp.ClientTimeout(total=None, sock_connect=CONNECT_TIMEOUT_SECONDS),
        )
    return clients[verify_ssl]
