import os
from dataclasses import dataclass

import chess
from dotenv import load_dotenv

PIECE_VALUES = {
    chess.PAWN: 1,
    chess.KNIGHT: 3,
    chess.BISHOP: 3,
    chess.ROOK: 5,
    chess.QUEEN: 9,
}

# what the openshock backend itself will accept (HardLimits.cs)
API_MIN_INTENSITY = 0
API_MAX_INTENSITY = 100
API_MIN_DURATION_MS = 300
API_MAX_DURATION_MS = 65535


def _str(name, default=""):
    return os.environ.get(name, default).strip()


def _int(name, default):
    raw = _str(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        raise ValueError(f"{name} should be a number, got {raw!r}")


def _bool(name, default):
    raw = _str(name).lower()
    if not raw:
        return default
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    raise ValueError(f"{name} should be true or false, got {raw!r}")


@dataclass(frozen=True)
class Settings:
    api_url: str
    api_token: str
    shocker_id_player: str
    shocker_id_opponent: str
    dry_run: bool

    intensity_by_piece: dict
    intensity_checkmate: int
    duration_base_ms: int
    duration_per_point_ms: int
    duration_checkmate_ms: int
    check_vibrate_intensity: int
    check_vibrate_duration_ms: int

    max_intensity: int
    max_duration_ms: int

    player_color: chess.Color
    opponent: str
    ai_depth: int
    stockfish_path: str
    stockfish_skill_level: int

    @classmethod
    def load(cls, env_file=None):
        # real env vars win over the file, same as dotenv's default
        load_dotenv(env_file) if env_file else load_dotenv()

        color = _str("PLAYER_COLOR", "white").lower()
        if color not in ("white", "black"):
            raise ValueError(f"PLAYER_COLOR should be white or black, got {color!r}")

        opponent = _str("OPPONENT", "ai").lower()
        if opponent not in ("ai", "human"):
            raise ValueError(f"OPPONENT should be ai or human, got {opponent!r}")

        return cls(
            api_url=_str("OPENSHOCK_API_URL", "https://api.openshock.app").rstrip("/"),
            api_token=_str("OPENSHOCK_API_TOKEN"),
            shocker_id_player=_str("OPENSHOCK_SHOCKER_ID"),
            shocker_id_opponent=_str("OPENSHOCK_SHOCKER_ID_OPPONENT"),
            dry_run=_bool("DRY_RUN", False),
            intensity_by_piece={
                chess.PAWN: _int("SHOCK_INTENSITY_PAWN", 10),
                chess.KNIGHT: _int("SHOCK_INTENSITY_KNIGHT", 25),
                chess.BISHOP: _int("SHOCK_INTENSITY_BISHOP", 25),
                chess.ROOK: _int("SHOCK_INTENSITY_ROOK", 40),
                chess.QUEEN: _int("SHOCK_INTENSITY_QUEEN", 70),
            },
            intensity_checkmate=_int("SHOCK_INTENSITY_CHECKMATE", 100),
            duration_base_ms=_int("SHOCK_DURATION_BASE_MS", 400),
            duration_per_point_ms=_int("SHOCK_DURATION_PER_POINT_MS", 150),
            duration_checkmate_ms=_int("SHOCK_DURATION_CHECKMATE_MS", 3000),
            check_vibrate_intensity=_int("CHECK_VIBRATE_INTENSITY", 35),
            check_vibrate_duration_ms=_int("CHECK_VIBRATE_DURATION_MS", 500),
            max_intensity=_int("SHOCK_MAX_INTENSITY", 50),
            max_duration_ms=_int("SHOCK_MAX_DURATION_MS", 2500),
            player_color=chess.WHITE if color == "white" else chess.BLACK,
            opponent=opponent,
            ai_depth=_int("AI_DEPTH", 3),
            stockfish_path=_str("STOCKFISH_PATH"),
            stockfish_skill_level=_int("STOCKFISH_SKILL_LEVEL", 5),
        )

    @property
    def shocker_by_color(self):
        out = {}
        if self.shocker_id_player:
            out[self.player_color] = self.shocker_id_player
        if self.shocker_id_opponent:
            out[not self.player_color] = self.shocker_id_opponent
        return out

    def problems(self):
        errs = []
        if not self.dry_run:
            if not self.api_token or self.api_token == "paste-your-token-here":
                errs.append("OPENSHOCK_API_TOKEN isn't set (needed unless DRY_RUN=true)")
            if not self.shocker_id_player:
                errs.append("OPENSHOCK_SHOCKER_ID isn't set (needed unless DRY_RUN=true)")
        if not API_MIN_INTENSITY <= self.max_intensity <= API_MAX_INTENSITY:
            errs.append(f"SHOCK_MAX_INTENSITY has to be {API_MIN_INTENSITY}-{API_MAX_INTENSITY}")
        if not API_MIN_DURATION_MS <= self.max_duration_ms <= API_MAX_DURATION_MS:
            errs.append(f"SHOCK_MAX_DURATION_MS has to be {API_MIN_DURATION_MS}-{API_MAX_DURATION_MS}")
        if any(v < 0 for v in self.intensity_by_piece.values()):
            errs.append("piece intensities can't be negative")
        if self.intensity_checkmate < 0 or self.check_vibrate_intensity < 0:
            errs.append("intensities can't be negative")
        if min(self.duration_base_ms, self.duration_per_point_ms, self.duration_checkmate_ms) < 0:
            errs.append("durations can't be negative")
        if self.ai_depth < 1:
            errs.append("AI_DEPTH has to be at least 1")
        return errs
