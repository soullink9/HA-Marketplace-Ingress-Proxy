"""The HTTP client the proxy talks to the apps with, and the one kind of address it won't talk to."""

import ipaddress
import socket
from urllib.parse import urlsplit

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.util.hass_dict import HassKey

from .const import CONNECT_TIMEOUT_SECONDS, DOMAIN, METADATA_ADDRESSES, READ_IDLE_TIMEOUT_SECONDS

_CLIENTS_KEY: HassKey[dict[bool, aiohttp.ClientSession]] = HassKey(f"{DOMAIN}_clients")


def async_get_client(hass: HomeAssistant, verify_ssl: bool) -> aiohttp.ClientSession:
    """Return the proxy's own client session, one for checked and one for unchecked certificates.

    Home Assistant's shared session isn't used, because the proxy needs a client that:
      - keeps no cookies of its own (DummyCookieJar), so one user's login to an app can't leak into another's
        requests; the cookies sent are exactly the ones cookies.py chose
      - leaves compressed replies compressed (auto_decompress=False), so they pass to the browser byte for byte
        with their Content-Encoding and Content-Length intact
      - has no total timeout, so live streams and big downloads aren't cut off; only connecting, and a reply going
        quiet for longer than READ_IDLE_TIMEOUT_SECONDS, are limited (websockets have their heartbeat instead)

    Home Assistant closes these sessions when it stops. A closed one is replaced, so a request never fails on it.
    """
    clients = hass.data.setdefault(_CLIENTS_KEY, {})
    if verify_ssl not in clients or clients[verify_ssl].closed:
        clients[verify_ssl] = async_create_clientsession(
            hass,
            verify_ssl=verify_ssl,
            cookie_jar=aiohttp.DummyCookieJar(),
            auto_decompress=False,
            timeout=aiohttp.ClientTimeout(
                total=None, sock_connect=CONNECT_TIMEOUT_SECONDS, sock_read=READ_IDLE_TIMEOUT_SECONDS
            ),
        )
    return clients[verify_ssl]


async def async_reaches_metadata_service(hass: HomeAssistant, url: str) -> bool:
    """Whether an app address leads to a cloud metadata service (METADATA_ADDRESSES), by IP or by name.

    A name is looked up, and refused if any of its addresses is one. A name that can't be looked up right now isn't
    refused here: the app may simply be down, and the proxy then can't reach it anyway.
    """
    host = urlsplit(url).hostname
    if not host:
        return False
    try:
        infos = await hass.loop.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except OSError:
        return False
    for info in infos:
        address = ipaddress.ip_address(str(info[4][0]).split("%", 1)[0])
        if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
            address = address.ipv4_mapped
        if str(address) in METADATA_ADDRESSES:
            return True
    return False
