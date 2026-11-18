"""The only way out for the audit's browser: an HTTP proxy that reaches public websites and nothing else.

Chromium is started with --proxy-server pointing here and no bypass (not even localhost), so every
connection it opens - pages, scripts, XHR, websockets - goes through `handle()`, which:
  - accepts ports 80 and 443 only;
  - resolves the host itself and refuses it unless EVERY address is public (no loopback, private,
    link-local such as the cloud metadata service, Docker networks, multicast...);
  - connects to the address it checked - a DNS answer that changes in between (rebinding) can't
    redirect the connection.
Plain-HTTP requests are forwarded with `Connection: close`, so one client connection never carries
a second request to another host.
"""
import asyncio
import ipaddress
import logging
import socket
from typing import Callable, Optional
from urllib.parse import urlsplit

log = logging.getLogger("egress")

ALLOWED_PORTS = {80, 443}
CONNECT_TIMEOUT = 8.0
MAX_HEADER_BYTES = 32 * 1024


def is_public(address: str) -> bool:
    ip = ipaddress.ip_address(address.split("%")[0])
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


class Refused(Exception):
    pass


async def resolve_public(host: str, port: int, allowed: Callable[[str], bool] = is_public) -> str:
    """The address to connect to, or Refused. All the host's addresses must be public."""
    if port not in ALLOWED_PORTS:
        raise Refused(f"port {port}")
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror:
        raise Refused(f"unknown host {host}")
    addresses = sorted(dict.fromkeys(info[4][0] for info in infos), key=lambda a: ":" in a)
    if not addresses:
        raise Refused(f"unknown host {host}")
    if not all(allowed(a) for a in addresses):
        raise Refused(f"non-public host {host}")
    return addresses[0]


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while True:
            chunk = await reader.read(65536)
            if not chunk:
                break
            writer.write(chunk)
            await writer.drain()
    except (ConnectionError, asyncio.CancelledError, OSError):
        pass
    finally:
        try:
            writer.close()
        except Exception:
            pass


async def _refuse(writer: asyncio.StreamWriter, reason: str) -> None:
    log.info("refused: %s", reason)
    try:
        writer.write(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
        await writer.drain()
    finally:
        writer.close()


def make_handler(allowed: Callable[[str], bool] = is_public):
    async def handle(client_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter) -> None:
        try:
            head = await asyncio.wait_for(client_reader.readuntil(b"\r\n\r\n"), timeout=CONNECT_TIMEOUT)
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, asyncio.TimeoutError, ConnectionError):
            client_writer.close()
            return
        if len(head) > MAX_HEADER_BYTES:
            return await _refuse(client_writer, "headers too large")
        lines = head.decode("latin-1").split("\r\n")
        try:
            method, target, version = lines[0].split(" ", 2)
        except ValueError:
            return await _refuse(client_writer, "bad request line")

        if method.upper() == "CONNECT":
            host, _, port_text = target.rpartition(":")
            host = host.strip("[]")
            try:
                port = int(port_text)
                address = await resolve_public(host, port, allowed)
                upstream_reader, upstream_writer = await asyncio.wait_for(asyncio.open_connection(address, port), timeout=CONNECT_TIMEOUT)
            except (Refused, ValueError) as error:
                return await _refuse(client_writer, str(error))
            except (OSError, asyncio.TimeoutError):
                client_writer.write(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
                client_writer.close()
                return
            client_writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            await client_writer.drain()
        else:
            parts = urlsplit(target)
            if parts.scheme != "http" or not parts.hostname:
                return await _refuse(client_writer, f"not an absolute http URL: {target[:80]}")
            port = parts.port or 80
            try:
                address = await resolve_public(parts.hostname, port, allowed)
                upstream_reader, upstream_writer = await asyncio.wait_for(asyncio.open_connection(address, port), timeout=CONNECT_TIMEOUT)
            except Refused as error:
                return await _refuse(client_writer, str(error))
            except (OSError, asyncio.TimeoutError):
                client_writer.write(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
                client_writer.close()
                return
            path = parts.path or "/"
            if parts.query:
                path += "?" + parts.query
            headers = [
                line for line in lines[1:]
                if line and line.split(":", 1)[0].strip().lower() not in ("proxy-connection", "connection", "keep-alive", "proxy-authorization")
            ]
            request = "\r\n".join([f"{method} {path} {version}", *headers, "Connection: close", "", ""])
            upstream_writer.write(request.encode("latin-1"))
            await upstream_writer.drain()

        await asyncio.gather(_pipe(client_reader, upstream_writer), _pipe(upstream_reader, client_writer))

    return handle


async def start(host: str = "127.0.0.1", port: int = 0, allowed: Optional[Callable[[str], bool]] = None) -> asyncio.base_events.Server:
    """Starts the proxy; `server.sockets[0].getsockname()[1]` is the port it listens on."""
    return await asyncio.start_server(make_handler(allowed or is_public), host, port, limit=MAX_HEADER_BYTES)
