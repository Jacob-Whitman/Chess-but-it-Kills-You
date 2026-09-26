import unittest

import chess

from chess_kills.game import ChessGame
from chess_kills.openshock import ControlType, OpenShockClient
from chess_kills.punishment import Punisher, PunishmentPolicy
from tests.helpers import FakeSession, make_settings


def play(sans):
    g = ChessGame()
    out = None
    for san in sans:
        out = g.push(g.board.parse_san(san))
    return g, out


class PolicyTests(unittest.TestCase):
    def test_piece_values(self):
        pol = PunishmentPolicy(make_settings())
        pawn = pol.for_capture(chess.PAWN)
        queen = pol.for_capture(chess.QUEEN)
        self.assertEqual(pawn.control_type, ControlType.SHOCK)
        self.assertEqual((pawn.intensity, pawn.duration_ms), (10, 400 + 150))
        self.assertEqual((queen.intensity, queen.duration_ms), (70, 400 + 150 * 9))

    def test_caps(self):
        pol = PunishmentPolicy(make_settings(max_intensity=30, max_duration_ms=1000))
        for p in (pol.for_capture(chess.QUEEN), pol.for_checkmate()):
            self.assertEqual(p.intensity, 30)
            self.assertEqual(p.duration_ms, 1000)

    def test_api_min_duration(self):
        pol = PunishmentPolicy(make_settings(duration_base_ms=0, duration_per_point_ms=0))
        self.assertEqual(pol.for_capture(chess.PAWN).duration_ms, 300)

    def test_check_can_be_turned_off(self):
        self.assertIsNone(PunishmentPolicy(make_settings(check_vibrate_intensity=0)).for_check())

    def test_mate_beats_capture(self):
        # scholar's mate, Qxf7# is a capture and mate at the same time
        _, out = play(["e4", "e5", "Bc4", "Nc6", "Qh5", "Nf6", "Qxf7#"])
        decided = PunishmentPolicy(make_settings()).for_outcome(out)
        self.assertEqual(len(decided), 1)
        color, p = decided[0]
        self.assertEqual(color, chess.BLACK)
        self.assertEqual(p.reason, "checkmated")

    def test_check_vibrates_the_checked_side(self):
        _, out = play(["e4", "d5", "exd5", "Qxd5", "Nc3", "Qe5+"])
        pol = PunishmentPolicy(make_settings())
        self.assertEqual(pol.for_outcome(out), [(chess.WHITE, pol.for_check())])


class PunisherTests(unittest.TestCase):
    def setUp(self):
        self.settings = make_settings()
        self.session = FakeSession()
        self.events = []
        client = OpenShockClient(self.settings.api_url, self.settings.api_token, session=self.session)
        self.punisher = Punisher(client, PunishmentPolicy(self.settings), self.settings.shocker_by_color,
                                 self.events.append, threaded=False)

    def test_only_shocks_the_side_with_a_shocker(self):
        g = ChessGame()
        for san in ("e4", "d5"):
            g.push(g.board.parse_san(san))

        # black loses a pawn, black has no shocker here
        self.punisher.handle(g.push(g.board.parse_san("exd5")))
        self.assertEqual(self.session.calls, [])
        self.assertTrue(any("no shocker configured" in e for e in self.events), self.events)

        # white loses a pawn, white is wired up
        self.punisher.handle(g.push(g.board.parse_san("Qxd5")))
        self.assertEqual(len(self.session.calls), 1)
        self.assertEqual(self.session.calls[0]["json"]["shocks"][0]["id"], self.settings.shocker_id_player)

    def test_payload(self):
        _, out = play(["e4", "d5", "exd5", "Qxd5"])
        self.punisher.handle(out)
        call = self.session.calls[0]
        self.assertEqual(call["method"], "POST")
        self.assertTrue(call["url"].endswith("/2/shockers/control"))
        self.assertEqual(call["json"]["shocks"][0], {
            "id": self.settings.shocker_id_player,
            "type": "Shock",
            "intensity": 10,
            "duration": 550,
            "exclusive": True,
        })
        self.assertEqual(call["json"]["customName"], "Chess: lost pawn")

    def test_emergency_stop_hits_everyone(self):
        self.punisher.shocker_by_color = {chess.WHITE: "aaa", chess.BLACK: "bbb"}
        self.punisher.emergency_stop()
        self.assertEqual(len(self.session.calls), 2)
        self.assertEqual({c["json"]["shocks"][0]["type"] for c in self.session.calls}, {"Stop"})

    def test_resigning_is_a_checkmate(self):
        self.punisher.punish_resignation(chess.WHITE)
        shock = self.session.calls[0]["json"]["shocks"][0]
        self.assertEqual(shock["intensity"], 100)
        self.assertEqual(self.session.calls[0]["json"]["customName"], "Chess: resigned")


if __name__ == "__main__":
    unittest.main()
