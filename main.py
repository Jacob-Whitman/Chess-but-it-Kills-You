import argparse
import logging
import sys
from dataclasses import replace

from chess_kills.config import Settings
from chess_kills.engine import make_opponent
from chess_kills.network import DEFAULT_PORT, DEFAULT_RELAY_PORT, OnlineConfig, parse_endpoint
from chess_kills.openshock import OpenShockClient, OpenShockError
from chess_kills.punishment import Punisher, PunishmentPolicy


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description="Chess, but losing pieces hurts.")
    ap.add_argument("--env", metavar="PATH", help="env file to use instead of ./.env")
    ap.add_argument("--list-shockers", action="store_true", help="print the shockers on your account and quit")
    ap.add_argument("--test-pulse", action="store_true", help="short vibrate on your shocker and quit")
    ap.add_argument("--dry-run", action="store_true", help="log punishments instead of sending them")
    ap.add_argument("-v", "--verbose", action="store_true")

    net = ap.add_argument_group("online (each player runs their own copy with their own .env)")
    net.add_argument("--host", nargs="?", const=str(DEFAULT_PORT), metavar="PORT",
                     help=f"host a game, default port {DEFAULT_PORT}. you play PLAYER_COLOR")
    net.add_argument("--join", metavar="HOST[:PORT]", help="join someone's game, you get the other colour")
    net.add_argument("--relay", metavar="HOST[:PORT]", help=f"go through relay.py (default port {DEFAULT_RELAY_PORT})")
    net.add_argument("--room", metavar="CODE", help="room code on the relay")
    return ap.parse_args(argv)


def online_config(args, settings):
    picked = [m for m in ("host", "join", "relay") if getattr(args, m) is not None]
    if not picked:
        if args.room:
            raise ValueError("--room only does something with --relay")
        return None
    if len(picked) > 1:
        raise ValueError("pick one of --host, --join, --relay")

    if args.host is not None:
        bind, port = parse_endpoint(args.host, DEFAULT_PORT)
        return OnlineConfig("host", bind, port, host_color=settings.player_color)
    if args.join is not None:
        host, port = parse_endpoint(args.join, DEFAULT_PORT)
        if not host:
            raise ValueError("--join needs an address, e.g. --join 203.0.113.7:5555")
        return OnlineConfig("join", host, port)
    host, port = parse_endpoint(args.relay, DEFAULT_RELAY_PORT)
    if not host:
        raise ValueError("--relay needs an address, e.g. --relay relay.example.org:5556")
    if not args.room:
        raise ValueError("--relay needs --room CODE")
    return OnlineConfig("relay", host, port, room=args.room, host_color=settings.player_color)


def main(argv=None):
    args = parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s", datefmt="%H:%M:%S")

    try:
        settings = Settings.load(args.env)
        if args.dry_run:
            settings = replace(settings, dry_run=True)
        online = online_config(args, settings)
    except ValueError as e:
        print(f"config error: {e}", file=sys.stderr)
        return 2

    problems = settings.problems()
    if problems:
        print("config problems:", file=sys.stderr)
        for p in problems:
            print("  -", p, file=sys.stderr)
        print("copy .env.example to .env and fill it in, or use --dry-run", file=sys.stderr)
        return 2

    client = OpenShockClient(settings.api_url, settings.api_token, dry_run=settings.dry_run)

    if args.list_shockers:
        try:
            shockers = client.list_shockers()
        except OpenShockError as e:
            print(f"failed: {e}", file=sys.stderr)
            return 1
        if not shockers:
            print("no shockers on this account")
            return 0
        print(f"{'ID':<38} {'Name':<20} {'Hub':<20} Paused")
        for s in shockers:
            print(f"{s.id:<38} {s.name:<20} {s.hub_name:<20} {s.is_paused}")
        return 0

    punisher = Punisher(client, PunishmentPolicy(settings), settings.shocker_by_color, on_event=print, threaded=False)

    if args.test_pulse:
        punisher.test_pulse(settings.player_color)
        return 0

    # imported here so --list-shockers etc. work on a box with no display
    from chess_kills.gui import ChessApp

    punisher.threaded = True
    if online:
        from chess_kills.network import OnlineSession
        app = ChessApp(settings, punisher, online=OnlineSession(online))
    else:
        opponent = make_opponent(settings) if settings.opponent == "ai" else None
        app = ChessApp(settings, punisher, opponent=opponent)
    app.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
