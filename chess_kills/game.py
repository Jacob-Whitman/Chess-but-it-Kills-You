from dataclasses import dataclass

import chess


@dataclass
class MoveOutcome:
    move: chess.Move
    san: str
    mover: chess.Color
    captured_piece: int | None
    captured_color: chess.Color | None
    is_check: bool
    is_checkmate: bool
    is_game_over: bool
    result: str | None


class ChessGame:
    def __init__(self):
        self.board = chess.Board()

    def reset(self):
        self.board.reset()

    @property
    def turn(self):
        return self.board.turn

    def legal_targets(self, from_square):
        return [m for m in self.board.legal_moves if m.from_square == from_square]

    def find_move(self, from_square, to_square, promotion=None):
        # promotion=None means "queen if this is a promotion, otherwise whatever"
        for m in self.board.legal_moves:
            if m.from_square != from_square or m.to_square != to_square:
                continue
            if m.promotion is None:
                return m
            if promotion is None and m.promotion == chess.QUEEN:
                return m
            if m.promotion == promotion:
                return m
        return None

    def push(self, move):
        b = self.board
        if move not in b.legal_moves:
            raise ValueError(f"illegal move {move.uci()}")

        mover = b.turn
        captured = None
        if b.is_en_passant(move):
            captured = chess.PAWN  # nothing on the target square in this case
        elif b.is_capture(move):
            captured = b.piece_type_at(move.to_square)

        san = b.san(move)
        b.push(move)

        over = b.is_game_over(claim_draw=True)
        return MoveOutcome(
            move=move,
            san=san,
            mover=mover,
            captured_piece=captured,
            captured_color=(not mover) if captured else None,
            is_check=b.is_check(),
            is_checkmate=b.is_checkmate(),
            is_game_over=over,
            result=b.result(claim_draw=True) if over else None,
        )
