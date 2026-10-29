"""`spark tunnel new|list|remove`: Cloudflare tunnels on the owner's domain, for the owner's own
install or for friends. Runs on the owner's machine with CF_API_TOKEN, CF_ACCOUNT_ID and CF_ZONE_ID;
a friend only ever gets the printed tunnel token.

Every tunnel is called spark-<name>, sends <name>.<zone> (and <name>-<suffix>.<zone> per --also) to
http://cosmos:80 on the machine that runs it, and answers 404 for anything else."""

import argparse
import re
import sys

from spark_cli import output
from spark_cli.cloudflare import CloudflareClient, tunnel_target
from spark_cli.errors import SparkError
from spark_cli.settings import Settings

PREFIX = "spark-"
SERVICE = "http://cosmos:80"
# One DNS label: Cloudflare's free certificate covers a single level below the zone.
LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("tunnel", help="create, list or remove Cloudflare tunnels")
    actions = parser.add_subparsers(dest="action", metavar="<action>", required=True)

    new = actions.add_parser("new", help="create a tunnel and print its token")
    new.add_argument("name", help="becomes <name>.<your domain>")
    new.add_argument(
        "--also",
        action="append",
        default=[],
        metavar="SUFFIX",
        help="extra hostname <name>-<suffix>.<your domain> (repeatable)",
    )
    new.add_argument(
        "--always-https",
        action="store_true",
        help="turn on Cloudflare's Always Use HTTPS for the whole domain",
    )
    new.set_defaults(handler=run_new)

    listing = actions.add_parser("list", help="show SPARK tunnels on this account")
    listing.set_defaults(handler=run_list)

    remove = actions.add_parser("remove", help="delete a tunnel and its DNS records")
    remove.add_argument("name")
    remove.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    remove.set_defaults(handler=run_remove)


def run_new(args: argparse.Namespace, settings: Settings) -> int:
    name = label(args.name, "name")
    labels = [name] + [label(f"{name}-{label(s, 'suffix')}", "name with suffix") for s in args.also]
    cf = CloudflareClient.from_settings(settings)
    zone = cf.zone_name()
    hostnames = [f"{x}.{zone}" for x in dict.fromkeys(labels)]

    existing = cf.tunnels(PREFIX + name)
    tunnel = existing[0] if existing else None
    # Never take over a name used by something else; check every one before changing anything.
    target = tunnel_target(tunnel["id"]) if tunnel else None
    missing = []
    for hostname in hostnames:
        records = cf.records(hostname)
        if not records:
            missing.append(hostname)
        elif target is None or any(r.get("content") != target for r in records):
            raise SparkError(
                f"{hostname} already has a DNS record that is not this tunnel's.",
                hint="Pick another name, or delete that record in the Cloudflare dashboard.",
            )

    if tunnel is None:
        tunnel = cf.create_tunnel(PREFIX + name)
        output.ok(f"Created tunnel {PREFIX}{name}")
    else:
        output.ok(f"Tunnel {PREFIX}{name} already exists; updating it")
    cf.set_ingress(tunnel["id"], hostnames, SERVICE)
    for hostname in missing:
        cf.create_cname(hostname, tunnel_target(tunnel["id"]), f"SPARK tunnel {PREFIX}{name}")
    output.ok("Hostnames: " + ", ".join(hostnames))
    if args.always_https:
        cf.always_use_https()
        output.ok(f"Always Use HTTPS is on for all of {zone}")

    token = tunnel.get("token") or cf.tunnel_token(tunnel["id"])
    output.info("Give this token only to whoever runs this SPARK (it is a password):")
    print()
    print(token)
    print()
    output.info("In their .env:")
    output.info(f"  SPARK_HOSTNAME={hostnames[0]}")
    output.info("  TUNNEL_TOKEN=<the token above>")
    output.info("  COMPOSE_PROFILES=tunnel")
    return 0


def run_list(args: argparse.Namespace, settings: Settings) -> int:
    cf = CloudflareClient.from_settings(settings)
    tunnels = [t for t in cf.tunnels() if t["name"].startswith(PREFIX)]
    if not tunnels:
        output.info("No SPARK tunnels on this account.")
        return 0
    names: dict[str, list[str]] = {}
    for record in cf.records():
        names.setdefault(str(record.get("content")), []).append(record["name"])
    rows = [("TUNNEL", "STATUS", "HOSTNAMES")]
    for tunnel in sorted(tunnels, key=lambda t: t["name"]):
        hostnames = sorted(names.get(tunnel_target(tunnel["id"]), []))
        rows.append((tunnel["name"], str(tunnel.get("status", "?")), ", ".join(hostnames) or "-"))
    widths = [max(len(row[i]) for row in rows) for i in range(2)]
    for row in rows:
        print(f"{row[0].ljust(widths[0])}  {row[1].ljust(widths[1])}  {row[2]}".rstrip())
    return 0


def run_remove(args: argparse.Namespace, settings: Settings) -> int:
    name = label(args.name.removeprefix(PREFIX), "name")
    cf = CloudflareClient.from_settings(settings)
    found = cf.tunnels(PREFIX + name)
    if not found:
        raise SparkError(f"There is no tunnel {PREFIX}{name}.", hint="See them: spark tunnel list")
    tunnel = found[0]
    records = [r for r in cf.records() if r.get("content") == tunnel_target(tunnel["id"])]
    hostnames = ", ".join(r["name"] for r in records) or "no DNS records"
    question = f"Delete tunnel {PREFIX}{name} ({hostnames})? Whoever runs it goes offline."
    if not args.yes and not confirm(question):
        output.info("Nothing removed.")
        return 1
    for record in records:
        cf.delete_record(record["id"])
    cf.delete_tunnel(tunnel["id"])
    output.ok(f"Removed tunnel {PREFIX}{name} and {len(records)} DNS record(s).")
    return 0


def label(value: str, what: str) -> str:
    value = value.strip().lower()
    if not LABEL.fullmatch(value):
        raise SparkError(
            f"'{value}' is not a valid {what}.",
            hint="Use letters, digits and dashes (not at the start or end), at most 63 in all.",
        )
    return value


def confirm(question: str) -> bool:
    if not sys.stdin.isatty():
        raise SparkError(
            "Not removing a tunnel without confirmation.",
            hint="Run it in a terminal to be asked, or add --yes.",
        )
    try:
        answer = input(f"{question} [y/N] ")
    except EOFError:
        return False
    return answer.strip().lower() in ("y", "yes")
