import asyncio
import socket
import threading
import unittest

import chess

from chess_kills.network import OnlineConfig, OnlineSession, parse_endpoint
from relay import Relay


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Recorder:
    def __init__(self):
        self.ready = threading.Event()
        self.closed = threading.Event()
        self.got_message = threading.Event()
        self.color = None
        self.messages = []
        self.close_reason = ""

    def on_ready(self, color):
        self.color = color
        self.ready.set()

    def on_message(self, msg):
        self.messages.append(msg)
        self.got_message.set()

    def on_closed(self, reason):
        self.close_reason = reason
        self.closed.set()


def start(session):
    rec = Recorder()
    session.start(rec.on_ready, rec.on_message, rec.on_closed)
    return rec


class ParseEndpointTests(unittest.TestCase):
    def test_forms(self):
        self.assertEqual(parse_endpoint("example.org:9000", 1), ("example.org", 9000))
        self.assertEqual(parse_endpoint("example.org", 5555), ("example.org", 5555))
        self.assertEqual(parse_endpoint("7777", 5555), ("", 7777))
        with self.assertRaises(ValueError):
            parse_endpoint("host:abc", 1)


class DirectTests(unittest.TestCase):
    def test_host_and_guest_talk(self):
        port = free_port()
        host = OnlineSession(OnlineConfig("host", "127.0.0.1", port, host_color=chess.BLACK))
        guest = OnlineSession(OnlineConfig("join", "127.0.0.1", port))
        hr, gr = start(host), start(guest)
        try:
            self.assertTrue(hr.ready.wait(5))
            self.assertTrue(gr.ready.wait(5))
            self.assertEqual(hr.color, chess.BLACK)
            self.assertEqual(gr.color, chess.WHITE)

            guest.send_move(chess.Move.from_uci("e2e4"), 0)
            self.assertTrue(hr.got_message.wait(5))
            self.assertEqual(hr.messages[0], {"type": "move", "uci": "e2e4", "ply": 0})

            host.send_resign()
            self.assertTrue(gr.got_message.wait(5))
            self.assertEqual(gr.messages[0]["type"], "resign")
        finally:
            guest.close()
            host.close()
        self.assertTrue(hr.closed.wait(5))
        self.assertIn("left", hr.close_reason)

    def test_nobody_listening(self):
        guest = OnlineSession(OnlineConfig("join", "127.0.0.1", free_port()))
        rec = start(guest)
        self.assertTrue(rec.closed.wait(10))
        self.assertIn("Could not connect", rec.close_reason)

    def test_close_while_hosting(self):
        host = OnlineSession(OnlineConfig("host", "127.0.0.1", free_port()))
        rec = start(host)
        host.close()
        host._thread.join(5)
        self.assertFalse(host._thread.is_alive())
        self.assertFalse(rec.ready.is_set())

    def test_garbage_from_peer(self):
        port = free_port()
        host = OnlineSession(OnlineConfig("host", "127.0.0.1", port))
        rec = start(host)
        with socket.create_connection(("127.0.0.1", port), timeout=5) as raw:
            raw.recv(1024)  # eat the start message
            raw.sendall(b"this is not json\n")
            self.assertTrue(rec.closed.wait(5))
        self.assertIn("malformed", rec.close_reason)
        host.close()


class RelayTests(unittest.TestCase):
    def setUp(self):
        self.port = free_port()
        self.loop = asyncio.new_event_loop()
        started = threading.Event()

        async def run():
            self.server = await asyncio.start_server(Relay().handle, "127.0.0.1", self.port)
            started.set()
            async with self.server:
                await self.server.serve_forever()

        self.thread = threading.Thread(target=lambda: self.loop.run_until_complete(run()), daemon=True)
        self.thread.start()
        self.assertTrue(started.wait(5))

    def tearDown(self):
        async def shutdown():
            self.server.close()
            await self.server.wait_closed()
            for t in asyncio.all_tasks():
                if t is not asyncio.current_task():
                    t.cancel()

        asyncio.run_coroutine_threadsafe(shutdown(), self.loop).result(5)
        self.thread.join(5)
        self.loop.close()

    def test_pairing(self):
        a = OnlineSession(OnlineConfig("relay", "127.0.0.1", self.port, room="Test-Room", host_color=chess.WHITE))
        b = OnlineSession(OnlineConfig("relay", "127.0.0.1", self.port, room="test-room"))
        ra = start(a)
        self.assertFalse(ra.ready.wait(0.3))  # nobody else there yet
        rb = start(b)
        try:
            self.assertTrue(ra.ready.wait(5))
            self.assertTrue(rb.ready.wait(5))
            self.assertTrue(a.is_host)
            self.assertFalse(b.is_host)
            self.assertEqual(ra.color, chess.WHITE)
            self.assertEqual(rb.color, chess.BLACK)

            a.send_move(chess.Move.from_uci("d2d4"), 0)
            self.assertTrue(rb.got_message.wait(5))
            self.assertEqual(rb.messages[0]["uci"], "d2d4")
        finally:
            a.close()
        self.assertTrue(rb.closed.wait(5))
        b.close()

    def test_bad_room_code(self):
        s = OnlineSession(OnlineConfig("relay", "127.0.0.1", self.port, room="!!"))
        rec = start(s)
        self.assertTrue(rec.closed.wait(5))
        self.assertIn("Relay refused", rec.close_reason)


if __name__ == "__main__":
    unittest.main()
