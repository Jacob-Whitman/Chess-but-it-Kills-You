from dataclasses import replace

import chess

from chess_kills.config import Settings

BASE = Settings(
    api_url="https://api.example.test",
    api_token="token",
    shocker_id_player="11111111-1111-1111-1111-111111111111",
    shocker_id_opponent="",
    dry_run=False,
    intensity_by_piece={chess.PAWN: 10, chess.KNIGHT: 25, chess.BISHOP: 25, chess.ROOK: 40, chess.QUEEN: 70},
    intensity_checkmate=100,
    duration_base_ms=400,
    duration_per_point_ms=150,
    duration_checkmate_ms=3000,
    check_vibrate_intensity=35,
    check_vibrate_duration_ms=500,
    max_intensity=100,
    max_duration_ms=5000,
    player_color=chess.WHITE,
    opponent="ai",
    ai_depth=2,
    stockfish_path="",
    stockfish_skill_level=5,
)


def make_settings(**kw):
    return replace(BASE, **kw)


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self.payload = payload
        self.text = text
        self.content = b"x" if payload is not None or text else b""

    def json(self):
        if self.payload is None:
            raise ValueError("no json")
        return self.payload


class FakeSession:
    def __init__(self, responses=None):
        self.headers = {}
        self.calls = []
        self.responses = responses or []

    def request(self, method, url, **kw):
        self.calls.append({"method": method, "url": url, **kw})
        if self.responses:
            return self.responses.pop(0)
        return FakeResponse(200, {"message": "ok"})
