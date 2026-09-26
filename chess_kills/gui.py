import logging
import queue
import sys
import threading
import tkinter as tk
from tkinter import messagebox

import chess

from .game import ChessGame
from .network import NetworkError
from .punishment import color_name

log = logging.getLogger(__name__)

SQ = 72
BOARD_PX = SQ * 8
LIGHT = "#f0d9b5"
DARK = "#b58863"
SELECTED = "#f6f669"
LAST_MOVE = "#cdd26a"
DOT = "#5a5a5a"
CHECK = "#e05050"

GLYPHS = {
    "K": "♔", "Q": "♕", "R": "♖", "B": "♗", "N": "♘", "P": "♙",
    "k": "♚", "q": "♛", "r": "♜", "b": "♝", "n": "♞", "p": "♟",
}
PIECE_FONT = "Segoe UI Symbol" if sys.platform == "win32" else "DejaVu Sans"


class PromotionDialog(tk.Toplevel):
    def __init__(self, parent, color):
        super().__init__(parent)
        self.title("Promote to")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()
        self.choice = chess.QUEEN
        for pt in (chess.QUEEN, chess.ROOK, chess.BISHOP, chess.KNIGHT):
            sym = chess.Piece(pt, color).symbol()
            tk.Button(self, text=GLYPHS[sym], font=(PIECE_FONT, 28), width=3,
                      command=lambda pt=pt: self.pick(pt)).pack(side=tk.LEFT, padx=4, pady=4)
        self.protocol("WM_DELETE_WINDOW", lambda: self.pick(chess.QUEEN))

    def pick(self, pt):
        self.choice = pt
        self.destroy()


class ChessApp:
    # opponent set   -> you vs the AI
    # online set     -> you vs someone over the network
    # neither        -> two people sharing the mouse
    def __init__(self, settings, punisher, opponent=None, online=None):
        if opponent and online:
            raise ValueError("can't have both an AI opponent and an online session")
        self.settings = settings
        self.punisher = punisher
        self.opponent = opponent
        self.online = online
        self.game = ChessGame()

        if online:
            self.human_colors = set()  # don't know yet, handshake decides
            self.flipped = False
        elif opponent:
            self.human_colors = {settings.player_color}
            self.flipped = settings.player_color == chess.BLACK
        else:
            self.human_colors = {chess.WHITE, chess.BLACK}
            self.flipped = False

        self.selected = None
        self.last_move = None
        self.ai_thinking = False
        self.online_connected = False
        self.finished_reason = None  # resign/disconnect, stuff the board itself doesn't know about
        self.game_id = 0  # bumped on new game so a late AI result gets thrown away

        # background threads push callables here, main thread runs them in poll_queue
        self.q = queue.Queue()

        self.root = tk.Tk()
        self.root.title("Chess But It Kills You")
        self.root.resizable(False, False)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.build_widgets()
        self.root.after(30, self.poll_queue)

        self.punisher.on_event = self.log_threadsafe
        self.log("Mode: " + ("DRY RUN (nothing is sent)" if settings.dry_run else "LIVE: shocks are real"))
        if online:
            self.log(self.describe_online())
        elif opponent:
            self.log(f"Opponent: {opponent.name}. You play {color_name(settings.player_color)}.")
        else:
            self.log("Hot-seat mode: two humans at one board.")
        self.redraw()
        self.update_status()

        if online:
            online.start(
                on_ready=lambda c: self.post(self.on_online_ready, c),
                on_message=lambda m: self.post(self.on_online_message, m),
                on_closed=lambda r: self.post(self.on_online_closed, r),
            )
        else:
            self.maybe_start_ai()

    def build_widgets(self):
        outer = tk.Frame(self.root, padx=8, pady=8)
        outer.pack()

        self.canvas = tk.Canvas(outer, width=BOARD_PX, height=BOARD_PX, highlightthickness=0)
        self.canvas.grid(row=0, column=0, rowspan=6, sticky="n")
        self.canvas.bind("<Button-1>", self.on_click)

        side = tk.Frame(outer, padx=8)
        side.grid(row=0, column=1, sticky="n")

        if self.settings.dry_run:
            banner, bg = "DRY RUN", "#3c7a3c"
        else:
            banner, bg = "LIVE SHOCKS ENABLED", "#a00000"
        tk.Label(side, text=banner, bg=bg, fg="white", font=("Segoe UI", 11, "bold"), width=34).pack(fill=tk.X, pady=(0, 6))

        self.status_var = tk.StringVar()
        tk.Label(side, textvariable=self.status_var, font=("Segoe UI", 12), anchor="w", width=34,
                 wraplength=300, justify=tk.LEFT).pack(fill=tk.X)

        self.punish_var = tk.StringVar(value="No punishment yet.")
        tk.Label(side, textvariable=self.punish_var, fg="#a00000", font=("Segoe UI", 10, "bold"), anchor="w",
                 wraplength=300, justify=tk.LEFT).pack(fill=tk.X, pady=(4, 8))

        tk.Label(side, text="Moves", anchor="w").pack(fill=tk.X)
        self.moves_text = tk.Text(side, width=40, height=12, state=tk.DISABLED, font=("Consolas", 10))
        self.moves_text.pack(fill=tk.X)

        tk.Label(side, text="Log", anchor="w").pack(fill=tk.X, pady=(8, 0))
        self.log_text = tk.Text(side, width=40, height=10, state=tk.DISABLED, font=("Consolas", 9), wrap=tk.WORD)
        self.log_text.pack(fill=tk.X)

        row = tk.Frame(side)
        row.pack(fill=tk.X, pady=(8, 0))
        tk.Button(row, text="New game", command=self.new_game).pack(side=tk.LEFT, padx=2)
        if self.online:
            tk.Button(row, text="Resign", command=self.resign).pack(side=tk.LEFT, padx=2)
        tk.Button(row, text="Test pulse", command=self.test_pulse).pack(side=tk.LEFT, padx=2)
        tk.Button(row, text="EMERGENCY STOP", bg="#a00000", fg="white", font=("Segoe UI", 9, "bold"),
                  command=self.emergency_stop).pack(side=tk.RIGHT, padx=2)

    # ---- board drawing

    def square_to_xy(self, sq):
        f, r = chess.square_file(sq), chess.square_rank(sq)
        col = 7 - f if self.flipped else f
        row = r if self.flipped else 7 - r
        return col * SQ, row * SQ

    def xy_to_square(self, x, y):
        if not (0 <= x < BOARD_PX and 0 <= y < BOARD_PX):
            return None
        col, row = x // SQ, y // SQ
        f = 7 - col if self.flipped else col
        r = row if self.flipped else 7 - row
        return chess.square(f, r)

    def redraw(self):
        b = self.game.board
        c = self.canvas
        c.delete("all")

        targets = {m.to_square for m in self.game.legal_targets(self.selected)} if self.selected is not None else set()
        checked_king = b.king(b.turn) if b.is_check() else None

        for sq in chess.SQUARES:
            x, y = self.square_to_xy(sq)
            fill = LIGHT if (chess.square_file(sq) + chess.square_rank(sq)) % 2 else DARK
            if self.last_move and sq in (self.last_move.from_square, self.last_move.to_square):
                fill = LAST_MOVE
            if sq == self.selected:
                fill = SELECTED
            if sq == checked_king:
                fill = CHECK
            c.create_rectangle(x, y, x + SQ, y + SQ, fill=fill, outline="")

            if sq in targets:
                r = SQ // 8
                cx, cy = x + SQ // 2, y + SQ // 2
                c.create_oval(cx - r, cy - r, cx + r, cy + r, fill=DOT, outline="")

            piece = b.piece_at(sq)
            if piece:
                c.create_text(x + SQ // 2, y + SQ // 2 + 2, text=GLYPHS[piece.symbol()],
                              font=(PIECE_FONT, int(SQ * 0.62)), fill="black")

        for i in range(8):
            fc = "hgfedcba"[i] if self.flipped else "abcdefgh"[i]
            rc = str(i + 1) if self.flipped else str(8 - i)
            c.create_text(i * SQ + SQ - 7, BOARD_PX - 8, text=fc, font=("Segoe UI", 8), fill="#333")
            c.create_text(6, i * SQ + 9, text=rc, font=("Segoe UI", 8), fill="#333")

    def game_finished(self):
        return self.finished_reason is not None or self.game.board.is_game_over(claim_draw=True)

    def update_status(self):
        b = self.game.board
        if self.finished_reason:
            self.status_var.set(self.finished_reason)
            return
        if b.is_game_over(claim_draw=True):
            result = b.result(claim_draw=True)
            if b.is_checkmate():
                self.status_var.set(f"Checkmate. {color_name(not b.turn)} wins ({result}).")
            else:
                self.status_var.set(f"Draw ({result}).")
            return
        if self.online and not self.online_connected:
            self.status_var.set(self.describe_online())
            return
        who = color_name(b.turn)
        chk = " (check!)" if b.is_check() else ""
        if self.ai_thinking:
            self.status_var.set(f"{who} to move. AI is thinking...")
        elif self.online and b.turn not in self.human_colors:
            self.status_var.set(f"{who} to move{chk}. Waiting for opponent...")
        elif self.online:
            self.status_var.set(f"Your move ({who}){chk}")
        else:
            self.status_var.set(f"{who} to move{chk}")

    def describe_online(self):
        cfg = self.online.config
        if cfg.mode == "host":
            return f"Hosting on port {cfg.port}. Waiting for an opponent to join..."
        if cfg.mode == "join":
            return f"Connecting to {cfg.host}:{cfg.port}..."
        return f"Relay {cfg.host}:{cfg.port}, room '{cfg.room}'. Waiting for an opponent..."

    def append(self, widget, text):
        widget.configure(state=tk.NORMAL)
        widget.insert(tk.END, text)
        widget.see(tk.END)
        widget.configure(state=tk.DISABLED)

    def log(self, msg):
        log.info(msg)
        self.append(self.log_text, msg + "\n")
        if "%" in msg and " ms " in msg:  # looks like a punishment line
            self.punish_var.set(msg)

    def post(self, fn, *args):
        self.q.put((fn, args))

    def poll_queue(self):
        try:
            while True:
                fn, args = self.q.get_nowait()
                fn(*args)
        except queue.Empty:
            pass
        self.root.after(30, self.poll_queue)

    def log_threadsafe(self, msg):
        if threading.current_thread() is threading.main_thread():
            self.log(msg)
        else:
            self.post(self.log, msg)

    def record_move(self, outcome):
        n = (len(self.game.board.move_stack) + 1) // 2
        if outcome.mover == chess.WHITE:
            self.append(self.moves_text, f"{n:>3}. {outcome.san:<8}")
        else:
            self.append(self.moves_text, f"{outcome.san}\n")

    # ---- clicking around

    def is_human_turn(self):
        return self.game.turn in self.human_colors and not self.ai_thinking

    def on_click(self, event):
        if self.game_finished() or not self.is_human_turn():
            return
        sq = self.xy_to_square(event.x, event.y)
        if sq is None:
            return
        b = self.game.board
        piece = b.piece_at(sq)

        if self.selected is None:
            if piece and piece.color == b.turn:
                self.selected = sq
                self.redraw()
            return

        if sq == self.selected:
            self.selected = None
            self.redraw()
            return

        move = self.game.find_move(self.selected, sq)
        if move:
            if move.promotion:
                dlg = PromotionDialog(self.root, b.turn)
                self.root.wait_window(dlg)
                move = self.game.find_move(self.selected, sq, dlg.choice) or move
            self.selected = None
            self.play(move)
            return

        # clicked somewhere else: either reselect one of our pieces or drop the selection
        self.selected = sq if (piece and piece.color == b.turn) else None
        self.redraw()

    def play(self, move):
        ply = len(self.game.board.move_stack)
        outcome = self.game.push(move)
        self.last_move = move
        self.record_move(outcome)

        if self.online and outcome.mover in self.human_colors:
            try:
                self.online.send_move(move, ply)
            except NetworkError as e:
                self.log(f"Could not send move: {e}")

        self.punisher.handle(outcome)
        self.redraw()
        self.update_status()

        if outcome.is_game_over:
            self.log(f"Game over: {outcome.result}")
            return
        self.maybe_start_ai()

    # ---- AI

    def maybe_start_ai(self):
        if not self.opponent or self.game.turn in self.human_colors or self.ai_thinking:
            return
        if self.game_finished():
            return
        self.ai_thinking = True
        self.update_status()
        board_copy = self.game.board.copy()
        gid = self.game_id

        def think():
            try:
                move = self.opponent.choose_move(board_copy)
            except Exception as e:
                log.exception("AI blew up")
                self.post(self.ai_failed, gid, str(e))
                return
            self.post(self.ai_done, gid, move)

        threading.Thread(target=think, daemon=True).start()

    def ai_done(self, gid, move):
        if gid != self.game_id:
            return
        self.ai_thinking = False
        self.play(move)

    def ai_failed(self, gid, err):
        if gid != self.game_id:
            return
        self.ai_thinking = False
        self.log(f"AI error: {err}")
        self.update_status()

    # ---- online

    def on_online_ready(self, color):
        self.online_connected = True
        self.human_colors = {color}
        self.flipped = color == chess.BLACK
        # only ever control the device of the person sitting at this computer
        if self.settings.shocker_id_player:
            self.punisher.shocker_by_color = {color: self.settings.shocker_id_player}
        else:
            self.punisher.shocker_by_color = {}
        self.log(f"Connected. You play {color_name(color)}.")
        self.redraw()
        self.update_status()

    def on_online_message(self, msg):
        kind = msg.get("type")
        if kind == "move":
            self.apply_remote_move(msg)
        elif kind == "new_game":
            self.log("Opponent started a new game.")
            self.reset_board()
        elif kind == "resign":
            if self.game_finished():
                return
            loser = self.game.turn if self.game.turn not in self.human_colors else not self.game.turn
            self.finish(f"Opponent resigned. {color_name(not loser)} wins.")
            self.punisher.punish_resignation(loser)
        else:
            self.log(f"Ignored unknown message type {kind!r} from opponent.")

    def apply_remote_move(self, msg):
        if self.game_finished():
            return
        if self.game.turn in self.human_colors:
            self.log("Opponent sent a move out of turn; ignored.")
            return
        expected = len(self.game.board.move_stack)
        if msg.get("ply") != expected:
            self.desync(f"opponent is at ply {msg.get('ply')}, we are at {expected}")
            return
        try:
            move = chess.Move.from_uci(str(msg.get("uci", "")))
        except ValueError:
            self.desync(f"unreadable move {msg.get('uci')!r}")
            return
        if move not in self.game.board.legal_moves:
            self.desync(f"illegal move {move.uci()}")
            return
        self.play(move)

    def desync(self, detail):
        self.log(f"Boards out of sync ({detail}). Disconnecting.")
        if self.online:
            self.online.close()
        self.finish("Connection dropped: boards were out of sync.")

    def on_online_closed(self, reason):
        self.online_connected = False
        self.log(reason)
        if not self.game_finished():
            self.finish(f"Disconnected: {reason}")

    def finish(self, reason):
        self.finished_reason = reason
        self.selected = None
        self.redraw()
        self.update_status()

    def resign(self):
        if not self.online or self.game_finished() or not self.online_connected:
            return
        if not messagebox.askyesno("Resign", "Resign this game? Resigning counts as being checkmated."):
            return
        (me,) = self.human_colors
        try:
            self.online.send_resign()
        except NetworkError as e:
            self.log(f"Could not send resignation: {e}")
        self.finish(f"You resigned. {color_name(not me)} wins.")
        self.punisher.punish_resignation(me)

    # ---- buttons

    def reset_board(self):
        self.game_id += 1
        self.ai_thinking = False
        self.game.reset()
        self.selected = None
        self.last_move = None
        self.finished_reason = None
        self.moves_text.configure(state=tk.NORMAL)
        self.moves_text.delete("1.0", tk.END)
        self.moves_text.configure(state=tk.DISABLED)
        self.punish_var.set("No punishment yet.")
        self.redraw()
        self.update_status()

    def new_game(self):
        if self.online:
            if not self.online_connected:
                self.log("Not connected; cannot start a new online game.")
                return
            try:
                self.online.send_new_game()
            except NetworkError as e:
                self.log(f"Could not notify opponent: {e}")
                return
        self.log("New game.")
        self.reset_board()
        self.maybe_start_ai()

    def test_pulse(self):
        color = next(iter(self.human_colors), self.settings.player_color)
        self.punisher.test_pulse(color)

    def emergency_stop(self):
        self.punisher.emergency_stop()

    def on_close(self):
        if not self.settings.dry_run and self.punisher.shocker_by_color:
            if messagebox.askyesno("Quit", "Send STOP to all shockers before quitting?"):
                self.punisher.emergency_stop()
        if self.online:
            self.online.close()
        if self.opponent:
            try:
                self.opponent.close()
            except Exception:
                pass
        self.root.destroy()

    def run(self):
        self.root.mainloop()
