"""The Developer container's only way out: a transparent HTTPS allowlist.

Runs inside its own small container (stdlib only; never imported by Aura). Inside the engine's
container, each allowed hostname resolves to this proxy and nothing else resolves at all. The proxy
reads the hostname from the TLS ClientHello (SNI) and, if it is on the list, pipes the still-encrypted
stream to the real host. It never decrypts anything, so certificates stay end-to-end; every other
destination is refused. Each decision is printed (one line) for Aura's audit log.
"""

import asyncio
import os

ALLOW = {h.strip().lower() for h in os.environ.get("ALLOW_HOSTS", "").split(",") if h.strip()}
MAX_HELLO = 64 * 1024
MAX_CONNECTIONS = 64


def sni(hello: bytes) -> str | None:
    """The server name in a TLS ClientHello, or None if it isn't one (or has none)."""
    try:
        if len(hello) < 5 or hello[0] != 0x16:
            return None
        p = 5 + 4 + 2 + 32  # record header, handshake header, version, random
        p += 1 + hello[p]  # session id
        p += 2 + int.from_bytes(hello[p : p + 2], "big")  # cipher suites
        p += 1 + hello[p]  # compression methods
        end = p + 2 + int.from_bytes(hello[p : p + 2], "big")
        p += 2
        while p + 4 <= min(end, len(hello)):
            kind = int.from_bytes(hello[p : p + 2], "big")
            size = int.from_bytes(hello[p + 2 : p + 4], "big")
            if kind == 0:  # server_name: list length (2), type (1), name length (2), name
                n = int.from_bytes(hello[p + 7 : p + 9], "big")
                return hello[p + 9 : p + 9 + n].decode("ascii").lower()
            p += 4 + size
    except (IndexError, UnicodeDecodeError):
        return None
    return None


async def read_hello(reader: asyncio.StreamReader) -> bytes:
    """A whole TLS record (a large ClientHello can arrive in several reads)."""
    head = await reader.readexactly(5)
    size = int.from_bytes(head[3:5], "big")
    if head[0] != 0x16 or size > MAX_HELLO:
        return head
    return head + await reader.readexactly(size)


async def pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
    except (ConnectionError, OSError):
        pass
    finally:
        writer.close()


slots = asyncio.Semaphore(MAX_CONNECTIONS)


async def handle(client_r: asyncio.StreamReader, client_w: asyncio.StreamWriter) -> None:
    async with slots:
        try:
            hello = await asyncio.wait_for(read_hello(client_r), 10)
            host = sni(hello)
            if host is None or host not in ALLOW:
                print(f"DENY {host}", flush=True)
                client_w.close()
                return
            print(f"ALLOW {host}", flush=True)
            up_r, up_w = await asyncio.wait_for(asyncio.open_connection(host, 443), 15)
            up_w.write(hello)
            await up_w.drain()
            await asyncio.gather(pipe(client_r, up_w), pipe(up_r, client_w))
        except (TimeoutError, asyncio.IncompleteReadError, ConnectionError, OSError) as e:
            print(f"ERROR {type(e).__name__}", flush=True)
            client_w.close()


async def main() -> None:
    server = await asyncio.start_server(handle, "0.0.0.0", 443)
    print(f"READY {','.join(sorted(ALLOW))}", flush=True)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
