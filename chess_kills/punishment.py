import logging
import threading
from dataclasses import dataclass

import chess

from .config import API_MIN_DURATION_MS, PIECE_VALUES
from .openshock import ControlType, OpenShockError

log = logging.getLogger(__name__)


def color_name(color):
    return "White" if color == chess.WHITE else "Black"


@dataclass
class Punishment:
    control_type: ControlType
    intensity: int
    duration_ms: int
    reason: str

    def describe(self):
        return f"{self.control_type.value} {self.intensity}% for {self.duration_ms} ms ({self.reason})"


class PunishmentPolicy:
    """Turns game events into Punishment objects. Doesn't talk to anything."""

    def __init__(self, settings):
        self.s = settings

    def cap(self, control_type, intensity, duration_ms, reason):
        intensity = max(0, min(intensity, self.s.max_intensity))
        duration_ms = max(API_MIN_DURATION_MS, min(duration_ms, self.s.max_duration_ms))
        return Punishment(control_type, intensity, duration_ms, reason)

    def for_capture(self, piece_type):
        if piece_type not in PIECE_VALUES:
            return None
        value = PIECE_VALUES[piece_type]
        intensity = self.s.intensity_by_piece.get(piece_type, 0)
        duration = self.s.duration_base_ms + self.s.duration_per_point_ms * value
        return self.cap(ControlType.SHOCK, intensity, duration, f"lost {chess.piece_name(piece_type)}")

    def for_checkmate(self):
        return self.cap(ControlType.SHOCK, self.s.intensity_checkmate, self.s.duration_checkmate_ms, "checkmated")

    def for_check(self):
        if self.s.check_vibrate_intensity <= 0:
            return None
        return self.cap(ControlType.VIBRATE, self.s.check_vibrate_intensity, self.s.check_vibrate_duration_ms, "in check")

    def for_outcome(self, outcome):
        # one thing per move, worst thing wins: mate > capture > check
        victim = not outcome.mover
        if outcome.is_checkmate:
            return [(victim, self.for_checkmate())]
        if outcome.captured_piece is not None:
            p = self.for_capture(outcome.captured_piece)
            if p and p.intensity > 0:
                return [(outcome.captured_color, p)]
        if outcome.is_check:
            p = self.for_check()
            if p:
                return [(victim, p)]
        return []


class Punisher:
    def __init__(self, client, policy, shocker_by_color, on_event=None, threaded=True):
        self.client = client
        self.policy = policy
        self.shocker_by_color = shocker_by_color
        self.on_event = on_event or (lambda msg: None)
        self.threaded = threaded

    def handle(self, outcome):
        decided = self.policy.for_outcome(outcome)
        for color, p in decided:
            shocker_id = self.shocker_by_color.get(color)
            if not shocker_id:
                self.on_event(f"{color_name(color)} {p.reason}: no shocker configured on this machine, skipped.")
                continue
            self._dispatch(shocker_id, color, p)
        return decided

    def _dispatch(self, shocker_id, color, p):
        prefix = "[DRY RUN] " if self.client.dry_run else ""
        self.on_event(f"{prefix}{color_name(color)}: {p.describe()}")

        def send():
            try:
                self.client.control(shocker_id, p.control_type, p.intensity, p.duration_ms,
                                    custom_name=f"Chess: {p.reason}")
            except (OpenShockError, ValueError) as e:
                log.error("punishment failed: %s", e)
                self.on_event(f"Punishment failed: {e}")

        if self.threaded:
            threading.Thread(target=send, daemon=True).start()
        else:
            send()

    def punish_resignation(self, color):
        # resigning = getting mated, otherwise everyone would just resign when a mate is coming
        shocker_id = self.shocker_by_color.get(color)
        if not shocker_id:
            self.on_event(f"{color_name(color)} resigned: no shocker configured on this machine, skipped.")
            return
        mate = self.policy.for_checkmate()
        self._dispatch(shocker_id, color, Punishment(mate.control_type, mate.intensity, mate.duration_ms, "resigned"))

    def emergency_stop(self):
        # deliberately not threaded, we want this to go out right now
        for color, shocker_id in self.shocker_by_color.items():
            try:
                self.client.stop(shocker_id)
                self.on_event(f"STOP sent to {color_name(color)}'s shocker.")
            except (OpenShockError, ValueError) as e:
                self.on_event(f"STOP failed for {color_name(color)}: {e}")

    def test_pulse(self, color, intensity=20, duration_ms=500):
        shocker_id = self.shocker_by_color.get(color)
        if not shocker_id:
            self.on_event(f"No shocker configured for {color_name(color)}.")
            return
        self._dispatch(shocker_id, color, self.policy.cap(ControlType.VIBRATE, intensity, duration_ms, "test pulse"))
