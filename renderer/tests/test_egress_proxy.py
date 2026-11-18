"""The audit browser's only way out reaches public websites, and nothing else."""
import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import egress_proxy  # noqa: E402


async def _ask(proxy_port: int, request: bytes) -> bytes:
    reader, writer = await asyncio.open_connection("127.0.0.1", proxy_port)
    writer.write(request)
    await writer.drain()
    answer = await asyncio.wait_for(reader.read(4096), timeout=5)
    writer.close()
    return answer


async def _upstream():
    """A local web server standing in for a website (it answers with its peer's request line)."""
    async def handle(reader, writer):
        head = await reader.readuntil(b"\r\n\r\n")
        line = head.split(b"\r\n")[0]
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: %d\r\n\r\n%s" % (len(line), line))
        await writer.drain()
        writer.close()
    return await asyncio.start_server(handle, "127.0.0.1", 0)


@pytest.mark.parametrize("address,public", [
    ("127.0.0.1", False), ("10.0.0.5", False), ("172.18.0.3", False), ("192.168.1.1", False),
    ("169.254.169.254", False), ("::1", False), ("fd00::1", False), ("::ffff:127.0.0.1", False),
    ("224.0.0.1", False), ("0.0.0.0", False), ("93.184.215.14", True), ("2606:4700::6810:84e5", True),
])
def test_only_public_addresses(address, public):
    assert egress_proxy.is_public(address) is public


def test_refuses_loopback_private_and_other_ports():
    async def scenario():
        proxy = await egress_proxy.start()
        port = proxy.sockets[0].getsockname()[1]
        try:
            for request in (
                b"CONNECT 127.0.0.1:443 HTTP/1.1\r\nHost: 127.0.0.1:443\r\n\r\n",
                b"CONNECT localhost:443 HTTP/1.1\r\n\r\n",
                b"CONNECT 169.254.169.254:80 HTTP/1.1\r\n\r\n",
                b"CONNECT [::1]:443 HTTP/1.1\r\n\r\n",
                b"CONNECT example.com:22 HTTP/1.1\r\n\r\n",
                b"GET http://127.0.0.1/ HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n",
                b"GET http://10.1.2.3:80/admin HTTP/1.1\r\n\r\n",
                b"GET /relative HTTP/1.1\r\nHost: example.com\r\n\r\n",
                b"GET ftp://example.com/ HTTP/1.1\r\n\r\n",
            ):
                assert (await _ask(port, request)).startswith(b"HTTP/1.1 403"), request
        finally:
            proxy.close()
    asyncio.run(scenario())


def test_forwards_to_an_allowed_host_with_connection_close():
    """With loopback allowed (as a stand-in for a public site), requests go through, in origin form."""
    async def scenario():
        site = await _upstream()
        site_port = site.sockets[0].getsockname()[1]
        egress_proxy.ALLOWED_PORTS.add(site_port)
        proxy = await egress_proxy.start(allowed=lambda a: a == "127.0.0.1")
        port = proxy.sockets[0].getsockname()[1]
        try:
            answer = await _ask(port, b"GET http://127.0.0.1:%d/page?x=1 HTTP/1.1\r\nHost: 127.0.0.1\r\nProxy-Connection: keep-alive\r\n\r\n" % site_port)
            assert answer.startswith(b"HTTP/1.1 200")
            assert answer.endswith(b"GET /page?x=1 HTTP/1.1")
            tunnel = await _ask(port, b"CONNECT 127.0.0.1:%d HTTP/1.1\r\n\r\n" % site_port)
            assert tunnel.startswith(b"HTTP/1.1 200 Connection Established")
        finally:
            egress_proxy.ALLOWED_PORTS.discard(site_port)
            proxy.close()
            site.close()
    asyncio.run(scenario())
