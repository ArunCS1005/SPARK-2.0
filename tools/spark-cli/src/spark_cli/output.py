"""Console output. Plain text so it reads the same in PowerShell, bash and CI logs."""

import sys


def info(message: str) -> None:
    print(f"  {message}")


def step(message: str) -> None:
    print(f"==> {message}")


def ok(message: str) -> None:
    print(f"[ok]   {message}")


def warn(message: str) -> None:
    print(f"[warn] {message}")


def fail(message: str) -> None:
    print(f"[fail] {message}", file=sys.stderr)


def error(message: str, hint: str | None = None) -> None:
    print(f"error: {message}", file=sys.stderr)
    if hint:
        print(f"hint:  {hint}", file=sys.stderr)
