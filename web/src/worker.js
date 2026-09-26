// Worker: serves /ws/<room> by handing the connection to that room's Durable Object.
// Everything under public/ is served as a static asset before this code runs.
//
// The Room is the referee. It holds the position, checks every move with chess.js
// and tells both players what happened. It never sees anyone's OpenShock token,
// each browser shocks its own player.

import { Chess } from "chess.js";

const ROOM_RE = /^[a-z0-9][a-z0-9_-]{1,63}$/;

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const m = url.pathname.match(/^\/ws\/([^/]+)$/);
    if (!m) return new Response("not found", { status: 404 });

    const code = decodeURIComponent(m[1]).toLowerCase();
    if (!ROOM_RE.test(code)) return new Response("bad room code", { status: 400 });
    if (request.headers.get("Upgrade") !== "websocket") return new Response("websocket only", { status: 426 });

    return env.ROOMS.get(env.ROOMS.idFromName(code)).fetch(request);
  },
};

export class Room {
  constructor(state) {
    this.state = state;
    this.chess = null;
    this.resigned = null;
    this.loaded = false;
    // keepalive pings get answered without waking us up
    state.setWebSocketAutoResponse(new WebSocketRequestResponsePair("ping", "pong"));
  }

  // state lives in storage so the object can hibernate / get evicted between moves
  async load() {
    if (this.loaded) return;
    const saved = (await this.state.storage.get("game")) || { moves: [], resigned: null };
    this.chess = new Chess();
    for (const san of saved.moves) this.chess.move(san);
    this.resigned = saved.resigned;
    this.loaded = true;
  }

  async save() {
    await this.state.storage.put("game", { moves: this.chess.history(), resigned: this.resigned });
  }

  players() {
    const out = {};
    for (const ws of this.state.getWebSockets()) {
      if (ws.readyState !== 1) continue;
      const a = ws.deserializeAttachment();
      if (a && a.color) out[a.color] = ws;
    }
    return out;
  }

  snapshot() {
    const c = this.chess;
    return {
      fen: c.fen(),
      moves: c.history(),
      turn: c.turn(),
      check: c.isCheck(),
      checkmate: c.isCheckmate(),
      draw: c.isDraw(),
      over: c.isGameOver() || !!this.resigned,
      resigned: this.resigned,
      players: Object.keys(this.players()),
    };
  }

  send(ws, msg) {
    try {
      ws.send(JSON.stringify(msg));
    } catch {}
  }

  broadcast(msg) {
    for (const ws of this.state.getWebSockets()) this.send(ws, msg);
  }

  async fetch(request) {
    await this.load();
    const taken = this.players();
    const color = !taken.w ? "w" : !taken.b ? "b" : null;

    const pair = new WebSocketPair();
    const [client, server] = Object.values(pair);
    this.state.acceptWebSocket(server);

    if (!color) {
      server.serializeAttachment({ color: null });
      this.send(server, { type: "error", message: "room is full" });
      server.close(4000, "room full");
    } else {
      server.serializeAttachment({ color });
      this.send(server, { type: "welcome", color, ...this.snapshot() });
      this.broadcast({ type: "presence", players: Object.keys(this.players()) });
    }
    return new Response(null, { status: 101, webSocket: client });
  }

  async webSocketMessage(ws, raw) {
    await this.load();
    const me = ws.deserializeAttachment()?.color;
    if (!me) return;

    let msg;
    try {
      msg = JSON.parse(raw);
    } catch {
      return this.send(ws, { type: "error", message: "bad json" });
    }

    if (msg.type === "move") {
      if (this.snapshot().over) return this.send(ws, { type: "error", message: "game is over" });
      if (this.chess.turn() !== me) return this.send(ws, { type: "error", message: "not your turn" });
      let mv;
      try {
        mv = this.chess.move({ from: msg.from, to: msg.to, promotion: msg.promotion || undefined });
      } catch {
        return this.send(ws, { type: "error", message: "illegal move" });
      }
      await this.save();
      this.broadcast({
        type: "state",
        ...this.snapshot(),
        last: { from: mv.from, to: mv.to, san: mv.san, captured: mv.captured || null, by: me },
      });
    } else if (msg.type === "new_game") {
      this.chess = new Chess();
      this.resigned = null;
      await this.save();
      this.broadcast({ type: "state", ...this.snapshot(), last: null, reset: me });
    } else if (msg.type === "resign") {
      if (this.snapshot().over) return;
      this.resigned = me;
      await this.save();
      this.broadcast({ type: "state", ...this.snapshot(), last: null });
    } else {
      this.send(ws, { type: "error", message: `unknown type ${msg.type}` });
    }
  }

  async webSocketClose() {
    await this.load();
    this.broadcast({ type: "presence", players: Object.keys(this.players()) });
  }

  async webSocketError() {
    await this.load();
    this.broadcast({ type: "presence", players: Object.keys(this.players()) });
  }
}
