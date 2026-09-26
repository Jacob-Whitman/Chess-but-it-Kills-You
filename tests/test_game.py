import random
import unittest

import chess

from chess_kills.engine import MinimaxOpponent
from chess_kills.game import ChessGame


class GameTests(unittest.TestCase):
    def test_en_passant_counts_as_pawn_capture(self):
        g = ChessGame()
        for san in ("e4", "a6", "e5", "d5"):
            g.push(g.board.parse_san(san))
        out = g.push(g.board.parse_san("exd6"))
        self.assertEqual(out.captured_piece, chess.PAWN)
        self.assertEqual(out.captured_color, chess.BLACK)

    def test_fools_mate(self):
        g = ChessGame()
        for san in ("f3", "e5", "g4"):
            g.push(g.board.parse_san(san))
        out = g.push(g.board.parse_san("Qh4#"))
        self.assertTrue(out.is_checkmate)
        self.assertTrue(out.is_game_over)
        self.assertEqual(out.result, "0-1")
        self.assertEqual(out.mover, chess.BLACK)

    def test_promotion_defaults_to_queen(self):
        g = ChessGame()
        g.board.set_fen("8/P6k/8/8/8/8/8/K7 w - - 0 1")
        self.assertEqual(g.find_move(chess.A7, chess.A8).promotion, chess.QUEEN)
        self.assertEqual(g.find_move(chess.A7, chess.A8, chess.KNIGHT).promotion, chess.KNIGHT)

    def test_illegal_move(self):
        with self.assertRaises(ValueError):
            ChessGame().push(chess.Move.from_uci("e2e5"))


class EngineTests(unittest.TestCase):
    def ai(self):
        return MinimaxOpponent(depth=2, rng=random.Random(0))

    def test_legal_move(self):
        b = chess.Board()
        self.assertIn(self.ai().choose_move(b), b.legal_moves)

    def test_mate_in_one(self):
        b = chess.Board("6k1/5ppp/8/8/8/8/8/R5K1 w - - 0 1")
        b.push(self.ai().choose_move(b))
        self.assertTrue(b.is_checkmate())

    def test_free_queen(self):
        b = chess.Board("4k3/8/8/3q4/8/8/8/3RK3 w - - 0 1")
        self.assertEqual(self.ai().choose_move(b).uci(), "d1d5")


if __name__ == "__main__":
    unittest.main()
