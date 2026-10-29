"""Entry point: `spark <command>`. Each command module registers its own sub-parser and sets
`handler`, a callable taking (args, settings) and returning an exit code."""

import argparse
import sys
from collections.abc import Sequence

from spark_cli import __version__, output
from spark_cli.commands import backup, doctor, init, setup, tunnel, user
from spark_cli.errors import SparkError
from spark_cli.settings import Settings

COMMANDS = (init, setup, user, tunnel, doctor, backup)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="spark",
        description="Set up and manage a SPARK home server.",
    )
    parser.add_argument("--version", action="version", version=f"spark {__version__}")
    subparsers = parser.add_subparsers(dest="command", metavar="<command>")
    for module in COMMANDS:
        module.register(subparsers)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handler = getattr(args, "handler", None)
    if handler is None:
        parser.print_help()
        return 2
    try:
        return int(handler(args, Settings.from_env()) or 0)
    except SparkError as exc:
        output.error(exc.message, exc.hint)
        return 1
    except KeyboardInterrupt:
        print(file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
