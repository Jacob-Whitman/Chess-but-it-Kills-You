"""
Online play. One JSON object per line over TCP.

    host  -> guest   {"type": "start", "protocol": 1, "host_color": "white"}
    guest -> host    {"type": "ready", "protocol": 1}
    both             {"type": "move", "uci": "e2e4", "ply": 0}
                     {"type": "new_game"} / {"type": "resign"} / {"type": "bye"}

With the relay there's an extra step up front:

    client -> relay  {"type": "room", "code": "purple-knight"}
    relay  -> client {"type": "paired", "role": "host"|"guest"}

Nothing about shockers or tokens ever goes over the wire. Each side shocks itself.
"""

import json
import logging
import socket
import threading
from dataclasses import dataclass

import chess

log = logging.getLogger(__name__)

PROTOCOL_VERSION = 1
DEFAULT_PORT = 5555
DEFAULT_RELAY_PORT = 5556
CONNECT_TIMEOUT = 15
HANDSHAKE_TIMEOUT = 30
MAX_LINE = 4096


class NetworkError(Exception):
    pass


@dataclass
class OnlineConfig:
    mode: str  # host / join / relay
    host: str = ""
    port: int = DEFAULT_PORT
    room: str = ""
    host_color: chess.Color = chess.WHITE


def parse_endpoint(text, default_port):
    text = text.strip()
    if text.isdigit():
        return "", int(text)
    host, sep, port = text.rpartition(":")
    if not sep:
        return text, default_port
    if not port.isdigit():
        raise ValueError(f"bad port in {text!r}")
    return host.strip("[]"), int(port)


def color_to_str(c):
    return "white" if c == chess.WHITE else "black"


def str_to_color(s):
    if s == "white":
        return chess.WHITE
    if s == "black":
        return chess.BLACK
    raise NetworkError(f"bad colour {s!r}")


class LineSocket:
    def __init__(self, sock):
        self.sock = sock
        self.reader = sock.makefile("rb")
        self.lock = threading.Lock()

    def send(self, msg):
        data = (json.dumps(msg, separators=(",", ":")) + "\n").encode()
        with self.lock:
            try:
                self.sock.sendall(data)
            except OSError as e:
                raise NetworkError(f"send failed: {e}")

    def recv(self, timeout=None):
        """Next message, or None if the other side hung up."""
        self.sock.settimeout(timeout)
        try:
            line = self.reader.readline(MAX_LINE + 1)
        except socket.timeout:
            raise NetworkError("Timed out waiting for the other player")
        except OSError:
            return None
        if not line:
            return None
        if len(line) > MAX_LINE:
            raise NetworkError("Peer sent an oversized message")
        try:
            msg = json.loads(line.decode())
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            raise NetworkError(f"Peer sent malformed data: {e}")
        if not isinstance(msg, dict) or "type" not in msg:
            raise NetworkError("Peer sent a message without a type")
        return msg

    def close(self):
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass


class OnlineSession:
    """Connects, does the handshake, then feeds incoming messages to callbacks
    from a background thread. The GUI is responsible for hopping back to its own thread."""

    def __init__(self, config):
        self.config = config
        self.conn = None
        self.my_color = None
        self.is_host = False
        self._listener = None
        self._closed = threading.Event()
        self._thread = None

    def start(self, on_ready, on_message, on_closed):
        self._thread = threading.Thread(target=self._run, args=(on_ready, on_message, on_closed), daemon=True)
        self._thread.start()

    def _run(self, on_ready, on_message, on_closed):
        try:
            self.conn, self.is_host = self._connect()
            self.my_color = self._handshake(self.conn, self.is_host)
        except NetworkError as e:
            if not self._closed.is_set():
                on_closed(str(e))
            self.close()
            return
        except OSError as e:
            if not self._closed.is_set():
                on_closed(f"Connection failed: {e}")
            self.close()
            return

        on_ready(self.my_color)

        reason = "Opponent disconnected."
        try:
            while not self._closed.is_set():
                msg = self.conn.recv()
                if msg is None:
                    break
                if msg.get("type") == "bye":
                    reason = "Opponent left the game."
                    break
                on_message(msg)
        except NetworkError as e:
            reason = str(e)
        finally:
            closed_by_us = self._closed.is_set()
            self.close()
            if not closed_by_us:
                on_closed(reason)

    def close(self):
        if self._closed.is_set():
            return
        self._closed.set()
        if self.conn:
            try:
                self.conn.send({"type": "bye"})
            except NetworkError:
                pass
            self.conn.close()
        if self._listener:
            try:
                self._listener.close()
            except OSError:
                pass

    def _connect(self):
        cfg = self.config
        if cfg.mode == "host":
            return self._accept(cfg.host, cfg.port), True
        if cfg.mode == "join":
            return LineSocket(self._dial(cfg.host, cfg.port)), False
        if cfg.mode == "relay":
            return self._via_relay(cfg.host, cfg.port, cfg.room)
        raise NetworkError(f"unknown mode {cfg.mode!r}")

    @staticmethod
    def _dial(host, port):
        if not host:
            raise NetworkError("No host address given")
        try:
            sock = socket.create_connection((host, port), timeout=CONNECT_TIMEOUT)
        except OSError as e:
            raise NetworkError(f"Could not connect to {host}:{port}: {e}")
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        return sock

    def _accept(self, bind_host, port):
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            listener.bind((bind_host, port))
            listener.listen(1)
        except OSError as e:
            listener.close()
            raise NetworkError(f"Could not listen on port {port}: {e}")
        self._listener = listener
        listener.settimeout(0.5)  # so close() can interrupt us
        log.info("hosting on port %d, waiting for someone to join", port)
        try:
            while not self._closed.is_set():
                try:
                    sock, addr = listener.accept()
                except socket.timeout:
                    continue
                log.info("opponent connected from %s", addr)
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                return LineSocket(sock)
        except OSError as e:
            raise NetworkError(f"Listener failed: {e}")
        finally:
            listener.close()
            self._listener = None
        raise NetworkError("Cancelled while waiting for an opponent")

    def _via_relay(self, host, port, room):
        if not room:
            raise NetworkError("A room code is required for relay play (--room)")
        conn = LineSocket(self._dial(host, port))
        conn.send({"type": "room", "code": room})
        log.info("waiting in relay room %r", room)
        try:
            # could be a long wait, poll so close() still works
            while True:
                if self._closed.is_set():
                    raise NetworkError("Cancelled while waiting in the relay room")
                try:
                    msg = conn.recv(timeout=1.0)
                    break
                except NetworkError as e:
                    if "Timed out" not in str(e):
                        raise
        except NetworkError:
            conn.close()
            raise
        if msg is None:
            conn.close()
            raise NetworkError("Relay closed the connection before pairing")
        if msg.get("type") == "error":
            conn.close()
            raise NetworkError(f"Relay refused: {msg.get('reason', 'unknown error')}")
        if msg.get("type") != "paired" or msg.get("role") not in ("host", "guest"):
            conn.close()
            raise NetworkError(f"Unexpected relay message: {msg}")
        return conn, msg["role"] == "host"

    def _handshake(self, conn, is_host):
        if is_host:
            conn.send({"type": "start", "protocol": PROTOCOL_VERSION, "host_color": color_to_str(self.config.host_color)})
            reply = conn.recv(timeout=HANDSHAKE_TIMEOUT)
            if reply is None:
                raise NetworkError("Opponent disconnected during handshake")
            if reply.get("type") != "ready":
                raise NetworkError(f"Expected 'ready', got {reply.get('type')!r}")
            self._check_protocol(reply)
            return self.config.host_color

        start = conn.recv(timeout=HANDSHAKE_TIMEOUT)
        if start is None:
            raise NetworkError("Host disconnected during handshake")
        if start.get("type") != "start":
            raise NetworkError(f"Expected 'start', got {start.get('type')!r}")
        self._check_protocol(start)
        host_color = str_to_color(start.get("host_color"))
        conn.send({"type": "ready", "protocol": PROTOCOL_VERSION})
        return not host_color

    @staticmethod
    def _check_protocol(msg):
        if msg.get("protocol") != PROTOCOL_VERSION:
            raise NetworkError(f"Protocol mismatch: they're on {msg.get('protocol')}, we're on {PROTOCOL_VERSION}")

    def _send(self, msg):
        if self.conn is None or self._closed.is_set():
            raise NetworkError("Not connected")
        self.conn.send(msg)

    def send_move(self, move, ply):
        self._send({"type": "move", "uci": move.uci(), "ply": ply})

    def send_new_game(self):
        self._send({"type": "new_game"})

    def send_resign(self):
        self._send({"type": "resign"})
