import logging
import random

import chess

log = logging.getLogger(__name__)

MATE = 100_000
SCORE = {
    chess.PAWN: 100,
    chess.KNIGHT: 320,
    chess.BISHOP: 330,
    chess.ROOK: 500,
    chess.QUEEN: 900,
    chess.KING: 0,
}
CENTER = {chess.D4, chess.E4, chess.D5, chess.E5}
NEAR_CENTER = {
    chess.C3, chess.D3, chess.E3, chess.F3,
    chess.C4, chess.F4, chess.C5, chess.F5,
    chess.C6, chess.D6, chess.E6, chess.F6,
}


def evaluate(board):
    # material plus a bit of "go towards the middle", from the side to move's point of view
    score = 0
    for sq, piece in board.piece_map().items():
        v = SCORE[piece.piece_type]
        if piece.piece_type in (chess.KNIGHT, chess.BISHOP, chess.PAWN):
            if sq in CENTER:
                v += 20
            elif sq in NEAR_CENTER:
                v += 8
        if piece.piece_type == chess.PAWN:
            rank = chess.square_rank(sq)
            v += 4 * (rank if piece.color == chess.WHITE else 7 - rank)
        score += v if piece.color == chess.WHITE else -v
    return score if board.turn == chess.WHITE else -score


def ordered_moves(board):
    # captures first (big victim, small attacker), then promotions, then the rest
    def key(m):
        p = 0
        if board.is_capture(m):
            victim = board.piece_type_at(m.to_square) or chess.PAWN
            attacker = board.piece_type_at(m.from_square) or chess.PAWN
            p += 10 * SCORE[victim] - SCORE[attacker]
        if m.promotion:
            p += SCORE[m.promotion]
        return -p
    return sorted(board.legal_moves, key=key)


def negamax(board, depth, alpha, beta):
    moves = ordered_moves(board)
    if not moves:
        return -(MATE + depth) if board.is_check() else 0
    if depth == 0:
        return evaluate(board)
    if board.is_insufficient_material():
        return 0

    best = -MATE * 2
    for m in moves:
        board.push(m)
        score = -negamax(board, depth - 1, -beta, -alpha)
        board.pop()
        if score > best:
            best = score
        if best > alpha:
            alpha = best
        if alpha >= beta:
            break
    return best


class MinimaxOpponent:
    name = "Built-in AI"

    def __init__(self, depth=3, rng=None):
        self.depth = max(1, depth)
        self.rng = rng or random.Random()

    def choose_move(self, board):
        best_moves = []
        best = -MATE * 2
        alpha, beta = -MATE * 2, MATE * 2
        for m in ordered_moves(board):
            board.push(m)
            penalty = 50 if board.is_repetition(2) else 0  # stop shuffling back and forth
            score = -negamax(board, self.depth - 1, -beta, -alpha) - penalty
            board.pop()
            if score > best:
                best, best_moves = score, [m]
            elif score == best:
                best_moves.append(m)
            alpha = max(alpha, score)
        if not best_moves:
            raise ValueError("no legal moves")
        return self.rng.choice(best_moves)

    def close(self):
        pass


class StockfishOpponent:
    name = "Stockfish"

    def __init__(self, path, skill_level=5, think_time=0.5):
        import chess.engine

        self.engine = chess.engine.SimpleEngine.popen_uci(path)
        try:
            self.engine.configure({"Skill Level": max(0, min(20, skill_level))})
        except chess.engine.EngineError:
            log.warning("engine has no Skill Level option, playing full strength")
        self.limit = chess.engine.Limit(time=think_time)

    def choose_move(self, board):
        result = self.engine.play(board, self.limit)
        if result.move is None:
            raise ValueError("engine gave no move")
        return result.move

    def close(self):
        self.engine.quit()


def make_opponent(settings):
    if settings.stockfish_path:
        try:
            return StockfishOpponent(settings.stockfish_path, settings.stockfish_skill_level)
        except Exception as e:
            log.warning("couldn't start stockfish at %s (%s), using built-in AI", settings.stockfish_path, e)
    return MinimaxOpponent(settings.ai_depth)
