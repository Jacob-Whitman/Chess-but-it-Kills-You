# Chess But It Kills You

Chess, except every time you lose a piece your OpenShock shocker goes off. Bigger
piece, bigger shock. Get mated and you get the full thing.

Defaults (all of it is in `.env`, and all of it gets clamped by the caps):

| you lose      | value | intensity | duration |
| ------------- | ----- | --------- | -------- |
| pawn          | 1     | 10%       | 550 ms   |
| knight/bishop | 3     | 25%       | 850 ms   |
| rook          | 5     | 40%       | 1150 ms  |
| queen         | 9     | 70%       | 1750 ms  |
| the game      |       | 100%      | 3000 ms  |

Getting put in check just vibrates. Resigning counts as getting mated, so no bailing out.

The caps (`SHOCK_MAX_INTENSITY`, `SHOCK_MAX_DURATION_MS`) default to 50% / 2500 ms and
win over everything else. Change them last.

## Setup

You need Python 3.11+, an OpenShock account with a paired shocker, and an API token with
the `shockers.use` permission (openshock.app -> Settings -> API Tokens).

```
pip install -r requirements.txt
cp .env.example .env
python main.py --list-shockers      # copy a shocker id into .env
python main.py --dry-run            # play a game, watch the log, nothing is sent
python main.py --test-pulse         # small vibrate to make sure it's actually you
python main.py
```

Shocks are live by default. `--dry-run` (or `DRY_RUN=true`) logs what would have been sent
instead.

## Playing

Click a piece, click where it goes. Promotions ask. The panel on the right has the move
list, a log, and the last thing that was sent to your shocker.

There's a big red **EMERGENCY STOP** button that sends `Stop` to every shocker the game
knows about.

Modes:

- `OPPONENT=ai` (default). You're `PLAYER_COLOR`, the other side is a small alpha-beta
  search. `AI_DEPTH=3` is about a second a move. Point `STOCKFISH_PATH` at a stockfish
  binary if you want to actually lose.
- `OPPONENT=human`. Two people, one mouse. `OPENSHOCK_SHOCKER_ID` is whoever plays
  `PLAYER_COLOR`, `OPENSHOCK_SHOCKER_ID_OPPONENT` is the other one. Either can be blank.

## Online

Each person runs the game on their own machine with their own `.env`. Only moves go over
the network, your token never leaves your computer and your game only ever shocks you.

Direct (same LAN, Tailscale, or a forwarded port):

```
python main.py --host                    # plays PLAYER_COLOR, port 5555
python main.py --join 192.168.1.20:5555  # gets the other colour
```

Or through the relay if nobody wants to forward ports. Run `relay.py` somewhere both of
you can reach and agree on a room name:

```
python relay.py                                              # on the server
python main.py --relay some.server:5556 --room purple-knight # both players
```

First one into the room hosts. The relay just copies bytes, it never sees anything
about your account.

Online games get a **Resign** button (see above re: what resigning costs you). New game
resets both boards. If the two boards ever disagree both sides drop the connection.

## Browser version (web/)

Same game, no install: one Cloudflare Worker serves the page and runs the rooms. Each
browser keeps its own token in localStorage and shocks its own player, the server only
ever sees moves. It's on Cloudflare's free plan.

```
cd web
npm install
npx wrangler login        # once
npx wrangler deploy       # prints your *.workers.dev url
```

Open the url in two browsers, type the same room code, play. First in is White. Shocks
are live by default here too; tick "Dry run" in the setup box to just watch the log.

To deploy on push instead, either connect the repo in the Cloudflare dashboard
(Worker -> Settings -> Builds, root directory `web`) or add `CLOUDFLARE_API_TOKEN` and
`CLOUDFLARE_ACCOUNT_ID` as repo secrets so `.github/workflows/deploy-web.yml` runs.
Pick one, not both.

`npx wrangler dev` runs the whole thing locally on http://127.0.0.1:8787.

## All the flags

```
--env PATH              use a different env file
--dry-run               don't actually send anything
--list-shockers         print shocker ids and quit
--test-pulse            20% vibrate, 500ms, quit
--host [PORT]           host online
--join HOST[:PORT]      join online
--relay HOST[:PORT]     go through a relay
--room CODE             room on the relay
```

Env vars you already have set beat the ones in the file.

## OpenShock API notes

Everything the client does is taken from the [OpenShock/API](https://github.com/OpenShock/API)
source:

- token goes in an `OpenShockToken` header (`Common/Constants/AuthConstants.cs`)
- `GET /1/shockers/own` lists hubs + shockers
- `POST /2/shockers/control` with

```json
{
  "shocks": [{"id": "<shocker guid>", "type": "Shock", "intensity": 25, "duration": 850, "exclusive": true}],
  "customName": "Chess: lost knight"
}
```

`type` is `Stop` / `Shock` / `Vibrate` / `Sound`. Intensity is 0-100, duration 300-65535 ms,
and the client refuses to send anything outside that.

## Layout

```
main.py                   cli + startup
relay.py                  standalone relay server
chess_kills/config.py     .env -> Settings
chess_kills/openshock.py  api client
chess_kills/game.py       python-chess wrapper, tells you what a move did
chess_kills/punishment.py event -> shock, caps, sending
chess_kills/engine.py     the ai
chess_kills/network.py    host/join/relay sessions
chess_kills/gui.py        tkinter
tests/                    python -m unittest discover -s tests
web/src/worker.js         cloudflare worker + Room durable object
web/public/index.html     the browser game
web/wrangler.toml         cloudflare config
```

## Don't be stupid

Start with `--dry-run`. Keep the caps low until you know what the numbers feel like. Make
a token that only has `shockers.use` on it. Don't commit `.env`. This is for adults using
their own hardware on themselves, read OpenShock's safety stuff before you strap anything on.
