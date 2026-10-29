"""`spark user add|list|remove`: Cosmos accounts.

Cosmos v0.23.4 facts used here (src/user/*.go, client/src/pages/config/users/usermanagement.jsx):
nicknames are validated with `required,min=3,max=32,alphanum` and stored lower-cased; a new
account gets a register key valid for 7 days that works once; the free tier refuses a new account
once 5 exist, pending invites included (409, code UC014)."""

import argparse
import contextlib
import re
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlencode

from spark_cli import output
from spark_cli.errors import SparkError
from spark_cli.settings import Settings, public_url
from spark_cli.state import State

if TYPE_CHECKING:
    from spark_cli.cosmos import CosmosClient

NICKNAME = re.compile(r"[A-Za-z0-9]{3,32}")
EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
RESERVED = {"anonymous"}  # FileBrowser refuses this login name (database/users/users.go)
ROLES = {0: "guest", 1: "user", 2: "admin"}  # utils/types.go
ADMIN = 2
FREE_TIER_ACCOUNTS = 5
INVITE_DAYS = 7
LIMIT_CODE = "UC014"


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("user", help="add, list or remove accounts")
    actions = parser.add_subparsers(dest="action", metavar="<action>", required=True)

    add = actions.add_parser("add", help="create an account and print its invite link")
    add.add_argument("name", help="3-32 letters or digits")
    add.add_argument("--admin", action="store_true", help="make the account an admin")
    add.add_argument("--email", default="", help="stored in Cosmos; no email is sent")
    add.set_defaults(handler=run_add)

    listing = actions.add_parser("list", help="show all accounts")
    listing.set_defaults(handler=run_list)

    remove = actions.add_parser("remove", help="delete an account (their files are kept)")
    remove.add_argument("name")
    remove.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    remove.set_defaults(handler=run_remove)


def run_add(args: argparse.Namespace, settings: Settings) -> int:
    nickname = validate_nickname(args.name)
    email = args.email.strip()
    if email and not EMAIL.fullmatch(email):
        raise SparkError(f"'{email}' is not a valid email address.")

    state = State(settings.state_dir)
    hostname = public_hostname(settings, state)
    cosmos = connect(settings, state, hostname)

    users = cosmos.list_users()
    if find_user(users, nickname):
        raise SparkError(
            f"An account named '{nickname}' already exists.",
            hint="Pick another name, or remove it first: spark user remove " + nickname,
        )
    limit = account_limit(cosmos.status())
    if len(users) >= limit:
        raise limit_error(limit)

    try:
        created = cosmos.create_user(nickname, email=email, admin=args.admin)
    except SparkError as exc:
        if LIMIT_CODE in f"{getattr(exc, 'code', '')} {exc.message}":
            raise limit_error(limit) from exc
        raise

    role = "admin" if args.admin else "user"
    output.ok(f"Created account '{nickname}' ({role}). {len(users) + 1} of {limit} accounts used.")
    key = created.get("registerKey")
    if not key:
        output.warn("Cosmos returned no invite key. Remove and add the account again.")
        return 1
    expires = parse_time(created.get("registerKeyExp"))
    when = f"on {expires:%Y-%m-%d}" if expires else f"in {INVITE_DAYS} days"
    output.info(f"Send this invite link to {nickname} privately (a direct message, not a group).")
    output.info("Whoever opens it first chooses the password:")
    print()
    output.info(invite_link(hostname, nickname, key))
    print()
    output.info(f"It works once and expires {when} ({INVITE_DAYS} days). If it expires unused:")
    output.info(f"spark user remove {nickname} --yes, then spark user add {nickname} again.")
    output.info(f"Their files will be in SPARK_DATA/users/{nickname} after their first sign-in.")
    return 0


def run_list(args: argparse.Namespace, settings: Settings) -> int:
    state = State(settings.state_dir)
    cosmos = connect(settings, state, public_hostname(settings, state))
    users = cosmos.list_users()
    limit = account_limit(cosmos.status())
    mfa = read_mfa(settings.volumes_dir / "cosmos-config" / "auth.db")

    for line in format_table(users, mfa, datetime.now(UTC)):
        print(line)
    print()
    output.info(f"{len(users)} of {limit} accounts used.")
    if mfa is None:
        output.info("MFA '?': Cosmos's API does not report it and its database was not readable.")
    return 0


def run_remove(args: argparse.Namespace, settings: Settings) -> int:
    nickname = args.name.strip().lower()  # Cosmos looks names up lower-cased
    state = State(settings.state_dir)
    cosmos = connect(settings, state, public_hostname(settings, state))

    users = cosmos.list_users()
    user = find_user(users, nickname)
    if user is None:
        raise SparkError(
            f"There is no account named '{nickname}'.", hint="See them: spark user list"
        )
    owner = str(state.get("setup.admin_user") or "").strip().lower()
    if nickname == owner:
        raise SparkError(
            f"'{nickname}' is the owner account created by spark setup; SPARK needs it.",
        )
    admins = [u for u in users if u.get("role") == ADMIN]
    if user.get("role") == ADMIN and len(admins) <= 1:
        raise SparkError(
            f"'{nickname}' is the last admin; removing it would lock everyone out of Cosmos.",
            hint="Make another account an admin first: spark user add <name> --admin",
        )

    if not args.yes and not confirm(f"Remove the account '{nickname}'?"):
        output.info("Nothing removed.")
        return 1

    cosmos.delete_user(nickname)
    output.ok(f"Removed account '{nickname}'.")
    output.info(f"Their files stay in SPARK_DATA/users/{nickname}, and FileBrowser keeps its own")
    output.info("record of them (Files > Settings > User management). Anyone added later as")
    output.info(f"'{nickname}' gets both, so delete them unless the same person is coming back.")
    return 0


def validate_nickname(name: str) -> str:
    """Cosmos's rule (user/create.go), checked on the name as typed; stored lower-cased."""
    if not NICKNAME.fullmatch(name):
        raise SparkError(
            f"'{name}' is not a valid account name.",
            hint="Use 3 to 32 letters or digits only: no spaces, dots, dashes or underscores.",
        )
    nickname = name.lower()
    if nickname in RESERVED:
        raise SparkError(f"'{nickname}' is reserved by FileBrowser; pick another name.")
    return nickname


def public_hostname(settings: Settings, state: State) -> str:
    """The hostname Cosmos answers on and invite links point at. Quick-tunnel mode uses the one
    `spark setup` read from the tunnel logs, since SPARK_HOSTNAME is not used there."""
    saved = str(state.get("setup.hostname") or "")
    if not saved:
        raise SparkError("SPARK is not set up yet.", hint="docker compose run --rm spark setup")
    if settings.hostname and not settings.quick_tunnel and settings.hostname != saved:
        raise SparkError(
            f"SPARK_HOSTNAME is {settings.hostname}, but Cosmos was set up for {saved}.",
            hint="Run docker compose run --rm spark setup first; it moves Cosmos to the new name.",
        )
    return saved


def connect(settings: Settings, state: State, hostname: str) -> "CosmosClient":
    token = state.get("cosmos.admin_token")
    if not token:
        raise SparkError(
            "No Cosmos admin token saved: run spark setup first.",
            hint="docker compose run --rm spark setup",
        )
    from spark_cli.cosmos import CosmosClient

    return CosmosClient.from_settings(settings, token=token, hostname=hostname)


def invite_link(hostname: str, nickname: str, register_key: str) -> str:
    """The same link Cosmos's dashboard hands out (usermanagement.jsx `sendlink`, form type 2)."""
    query = urlencode({"t": "2", "nickname": nickname, "key": register_key})
    return f"{public_url(hostname)}/cosmos-ui/register?{query}"


def account_limit(status: dict[str, Any]) -> int:
    """Seats from GET /api/status (`LicenceNumber`, 5 without a licence; src/system.go)."""
    try:
        return int(status.get("LicenceNumber") or FREE_TIER_ACCOUNTS)
    except (TypeError, ValueError):
        return FREE_TIER_ACCOUNTS


def limit_error(limit: int) -> SparkError:
    if limit == FREE_TIER_ACCOUNTS:
        message = f"Cosmos's free tier allows {limit} accounts including yours."
    else:
        message = f"Your Cosmos licence allows {limit} accounts including yours."
    return SparkError(message, hint="Remove one with: spark user remove <name>")


def find_user(users: list[dict], nickname: str) -> dict | None:
    return next((u for u in users if str(u.get("nickname", "")).lower() == nickname), None)


def parse_time(value: Any) -> datetime | None:
    """A Cosmos JSON timestamp, or None when missing or Go's zero time (0001-01-01)."""
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed if parsed.year > 1970 else None


def account_status(user: dict, now: datetime) -> str:
    """Same rules as the Cosmos dashboard: registered once `registeredAt` is set, otherwise the
    invite is pending until `registerKeyExp`."""
    if parse_time(user.get("registeredAt")):
        return "registered"
    expires = parse_time(user.get("registerKeyExp"))
    return "invite pending" if expires and expires > now else "invite expired"


def format_table(users: list[dict], mfa: dict[str, bool] | None, now: datetime) -> list[str]:
    rows = [("NAME", "ROLE", "STATUS", "MFA", "CREATED")]
    for user in sorted(users, key=lambda u: str(u.get("nickname", ""))):
        name = str(user.get("nickname", ""))
        role = user.get("role")
        created = parse_time(user.get("createdAt"))
        rows.append(
            (
                name,
                ROLES.get(role, str(role)) if isinstance(role, int) else "?",
                account_status(user, now),
                "?" if mfa is None else ("on" if mfa.get(name) else "off"),
                f"{created:%Y-%m-%d}" if created else "-",
            )
        )
    widths = [max(len(cell) for cell in column) for column in zip(*rows, strict=True)]
    return [
        "  ".join(cell.ljust(width) for cell, width in zip(row, widths, strict=True)).rstrip()
        for row in rows
    ]


def read_mfa(db_path: Path) -> dict[str, bool] | None:
    """MFA state per account, read-only from Cosmos's own database: its API never returns it
    (`MFAKey` and `Was2FAVerified` are `json:"-"` in utils/types.go). Cosmos treats MFA as on when
    both are set (user/token.go); table and columns from utils/store_schema.go. None when the
    database cannot be read."""
    if not db_path.is_file():
        return None
    try:
        uri = f"{db_path.resolve().as_uri()}?mode=ro"
        with contextlib.closing(sqlite3.connect(uri, uri=True, timeout=5)) as db:
            rows = db.execute(
                "SELECT nickname, mfa_key != '' AND was_2fa_verified != 0 FROM users"
            ).fetchall()
    except sqlite3.Error:
        return None
    return {str(name): bool(on) for name, on in rows}


def confirm(question: str) -> bool:
    if not sys.stdin.isatty():
        raise SparkError(
            "Not removing an account without confirmation.",
            hint="Run it in a terminal to be asked, or add --yes.",
        )
    try:
        answer = input(f"{question} [y/N] ")
    except EOFError:
        return False
    return answer.strip().lower() in ("y", "yes")
