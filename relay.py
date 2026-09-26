"""
Relay server so two people can find each other with a room code instead of
messing with port forwarding.

    python relay.py            # listens on 0.0.0.0:5556
    python relay.py --port 9000

Then both players:  python main.py --relay your.server:5556 --room whatever

First one into the room is the host. After pairing the relay just shovels bytes
back and forth and doesn't look at them.
"""

import argparse
import asyncio
import json
import logging
import re
from dataclasses import dataclass, field

from chess_kills.network import DEFAULT_RELAY_PORT, MAX_LINE

log = logging.getLogger("relay")

ROOM_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,63}$")
ROOM_WAIT = 15 * 60
HELLO_TIMEOUT = 30


@dataclass
class Waiting:
    reader: asyncio.StreamReader
    writer: asyncio.StreamWriter
    paired: asyncio.Future = field(default_factory=lambda: asyncio.get_running_loop().create_future())


def peer(writer):
    info = writer.get_extra_info("peername")
    return f"{info[0]}:{info[1]}" if info else "?"


async def send(writer, msg):
    writer.write((json.dumps(msg) + "\n").encode())
    await writer.drain()


async def refuse(writer, reason):
    try:
        await send(writer, {"type": "error", "reason": reason})
    except OSError:
        pass
    writer.close()


async def pipe(src, dst):
    try:
        while True:
            data = await src.read(MAX_LINE)
            if not data:
                break
            dst.write(data)
            await dst.drain()
    except OSError:
        pass
    finally:
        dst.close()


class Relay:
    def __init__(self):
        self.rooms = {}

    async def handle(self, reader, writer):
        who = peer(writer)
        try:
            line = await asyncio.wait_for(reader.readline(), HELLO_TIMEOUT)
            hello = json.loads(line.decode()) if line else {}
        except (asyncio.TimeoutError, UnicodeDecodeError, json.JSONDecodeError, OSError):
            hello = {}
        if not isinstance(hello, dict) or hello.get("type") != "room":
            await refuse(writer, "expected a room message")
            return
        code = str(hello.get("code", "")).strip().lower()
        if not ROOM_RE.match(code):
            await refuse(writer, "room code must be 2-64 chars: a-z, 0-9, - or _")
            return

        waiting = self.rooms.pop(code, None)
        if waiting is None:
            await self.wait_for_partner(code, reader, writer, who)
            return

        # someone's already here, hand ourselves over and let their coroutine run the show
        log.info("room %s: %s joined, pairing with %s", code, who, peer(waiting.writer))
        done = asyncio.get_running_loop().create_future()
        waiting.paired.set_result((reader, writer, done))
        await done

    async def wait_for_partner(self, code, reader, writer, who):
        waiting = Waiting(reader, writer)
        self.rooms[code] = waiting
        log.info("room %s: %s waiting", code, who)

        # client shouldn't send anything until paired, so if this read finishes they've left
        watchdog = asyncio.ensure_future(reader.read(1))
        try:
            finished, _ = await asyncio.wait({waiting.paired, watchdog}, timeout=ROOM_WAIT,
                                             return_when=asyncio.FIRST_COMPLETED)
        finally:
            if self.rooms.get(code) is waiting:
                del self.rooms[code]

        if waiting.paired not in finished:
            watchdog.cancel()
            if not finished:
                await refuse(writer, "nobody joined the room in time")
            else:
                writer.close()
            log.info("room %s: %s left before anyone joined", code, who)
            return

        watchdog.cancel()
        guest_reader, guest_writer, done = waiting.paired.result()
        try:
            await send(writer, {"type": "paired", "role": "host"})
            await send(guest_writer, {"type": "paired", "role": "guest"})
            await asyncio.gather(pipe(reader, guest_writer), pipe(guest_reader, writer))
        except OSError as e:
            log.info("room %s: connection error %s", code, e)
        finally:
            writer.close()
            guest_writer.close()
            if not done.done():
                done.set_result(None)
            log.info("room %s: closed", code)


async def serve(host, port):
    relay = Relay()
    server = await asyncio.start_server(relay.handle, host, port)
    log.info("listening on %s", ", ".join(str(s.getsockname()) for s in server.sockets or []))
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=DEFAULT_RELAY_PORT)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", datefmt="%H:%M:%S")
    try:
        asyncio.run(serve(args.host, args.port))
    except KeyboardInterrupt:
        pass
