"""`spark setup`: first-run setup of Cosmos, then reconcile everything SPARK manages. Safe to run
again at any time; a run with nothing to change writes nothing and restarts nothing."""

import argparse
import getpass
import re
import string
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from spark_cli import filebrowser, output, seed
from spark_cli.cosmos import CosmosClient
from spark_cli.docker_api import DockerClient
from spark_cli.errors import SparkError
from spark_cli.settings import Settings, is_laptop_only, public_url
from spark_cli.state import State

# Cosmos's own rules (newInstall.go AdminJSON).
NICKNAME = re.compile(r"[A-Za-z0-9]{3,32}")
PASSWORD_SPECIALS = "~!@#$%^&*()_+=-{[}]:;\"'<>.?/,"

FILES_ROUTE = "SPARK Files"
QUICK_TUNNEL_URL = re.compile(r"https://([a-z0-9-]+\.trycloudflare\.com)")


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("setup", help="Set up Cosmos and FileBrowser (safe to re-run)")
    parser.add_argument("--admin-user", help="owner account name (first run only)")
    parser.add_argument("--admin-email", help="owner email (first run only, optional)")
    parser.add_argument(
        "--require-mfa",
        action="store_true",
        help="make two-factor login mandatory for every account",
    )
    parser.set_defaults(handler=run)


def run(args: argparse.Namespace, settings: Settings) -> int:
    docker = DockerClient.from_socket(settings.docker_socket, settings.project)
    if not docker.ping():
        raise SparkError(
            "Cannot reach the Docker engine.",
            hint="Run it through compose: docker compose run --rm spark setup",
        )
    return Setup(settings, docker, State(settings.state_dir)).run(args)


class Setup:
    def __init__(
        self,
        settings: Settings,
        docker: DockerClient,
        state: State,
        *,
        cosmos_factory: Callable[..., CosmosClient] = CosmosClient.from_settings,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.settings = settings
        self.docker = docker
        self.state = state
        self.cosmos_factory = cosmos_factory
        self.sleep = sleep

    def run(self, args: argparse.Namespace) -> int:
        cosmos_container = self.docker.require_container("cosmos")["Id"]
        hostname = self.public_hostname()
        self.check_laptop_only(hostname)
        if hostname and self.docker.add_network_alias(cosmos_container, hostname):
            output.ok(f"Cosmos answers as {hostname} inside Docker")

        # Talk to Cosmos under the name it currently serves; it changes below.
        cosmos = self.cosmos_factory(
            self.settings, hostname=self.state.get("setup.hostname") or hostname or "localhost"
        )
        cosmos.wait_until_up()

        if cosmos.is_new_install():
            cosmos = self.first_run(cosmos, args, hostname or "localhost")
        else:
            token = self.state.get("cosmos.admin_token")
            if not token:
                raise SparkError(
                    "Cosmos is already set up, but SPARK has no admin token for it.",
                    hint="See docs/guides/troubleshooting.md, 'Lost admin token'.",
                )
            cosmos = cosmos.with_token(token)

        changed = self.reconcile_config(cosmos, hostname or "localhost", args.require_mfa)
        # From here on Cosmos only answers to the new name.
        cosmos.hostname = hostname or "localhost"
        if changed:
            output.ok("Cosmos settings updated")
            self.restart_pause(cosmos)
        else:
            output.ok("Cosmos settings already up to date")

        if not hostname:
            output.warn("No public hostname: FileBrowser is not set up.")
            output.info("Set SPARK_HOSTNAME (own or friend's domain) or use the quick-tunnel")
            output.info("profile, then run spark setup again. Cosmos: " + self.local_url())
            self.state.set("setup.hostname", "localhost")
            return 0

        self.setup_files(cosmos, hostname)
        self.state.set("setup.hostname", hostname)
        output.step("SPARK is set up")
        url = public_url(hostname)
        output.info(f"Open:    {url}    Files: {url}/files")
        output.info(f"On this laptop: {self.local_url()}")
        output.info("Next: invite people with  docker compose run --rm spark user add <name>")
        return 0

    # -- steps -------------------------------------------------------------------------------

    def public_hostname(self) -> str:
        if not self.settings.quick_tunnel:
            return self.settings.hostname
        container = self.docker.require_container("quick-tunnel")["Id"]
        for _ in range(30):
            match = QUICK_TUNNEL_URL.search(self.docker.logs(container, tail=200))
            if match:
                output.ok(f"Quick tunnel: https://{match.group(1)} (changes when it restarts)")
                return match.group(1)
            self.sleep(2)
        raise SparkError(
            "The quick tunnel has not printed its URL yet.",
            hint="Check it: docker compose logs quick-tunnel",
        )

    def check_laptop_only(self, hostname: str) -> None:
        if not is_laptop_only(hostname):
            return
        if hostname == "localhost":
            raise SparkError(
                "SPARK_HOSTNAME cannot be localhost itself.",
                hint="Use a name under it, such as spark.localhost: FileBrowser must reach "
                "Cosmos by that name, and inside its own container localhost is FileBrowser.",
            )
        if self.settings.http_port != "80":
            raise SparkError(
                f"{hostname} only works with SPARK_HTTP_PORT=80.",
                hint="Cosmos's sign-in links carry no port, so browsers must find SPARK on port "
                "80. Set SPARK_HTTP_PORT=80 in .env, run docker compose up -d, then setup again.",
            )

    def first_run(self, cosmos: CosmosClient, args: argparse.Namespace, hostname: str):
        nickname = validate_nickname(args.admin_user or self.settings.admin_user or ask_name())
        email = args.admin_email or self.settings.admin_email
        password = self.settings.admin_password or ask_password()
        validate_password(password)

        token = cosmos.setup(nickname=nickname, password=password, email=email, hostname=hostname)
        self.state.set("cosmos.admin_token", token)
        self.state.set("setup.admin_user", nickname.lower())
        self.state.set("setup.hostname", hostname)
        output.ok(f"Cosmos set up; owner account '{nickname.lower()}'")
        # Cosmos exits a second after answering and its restart policy brings it back.
        cosmos = cosmos.with_token(token)
        cosmos.hostname = hostname
        self.restart_pause(cosmos, seconds=4)
        return cosmos

    def reconcile_config(self, cosmos: CosmosClient, hostname: str, require_mfa: bool) -> bool:
        cosmos_container = self.docker.require_container("cosmos")["Id"]
        network, _ = self.docker.project_network(cosmos_container)
        subnets = self.docker.network_subnets(network)
        data_path = self.apps_data_path()

        def mutate(config: dict[str, Any]) -> None:
            seed.enforce_safety(config)
            http = config.setdefault("HTTPConfig", {})
            http["Hostname"] = hostname
            http["AllowHTTPLocalIPAccess"] = True
            # Cosmos sees every request coming from cloudflared; trust X-Forwarded-For from the
            # project network only, so its brute-force protection sees real client IPs.
            http["TrustedProxies"] = subnets
            if data_path:
                config.setdefault("DockerConfig", {})["DefaultDataPath"] = data_path
            if self.settings.server_country:
                config["ServerCountry"] = self.settings.server_country
            if require_mfa:
                config["RequireMFA"] = True

        return cosmos.update_config(mutate)

    def apps_data_path(self) -> str:
        """Where apps from Cosmos's app store keep their data: SPARK_DATA/apps instead of
        Cosmos's default /cosmos-storage at the root of the host's disk. Needs SPARK_DATA as an
        absolute host path, since the Docker engine resolves it."""
        raw = self.settings.data_host_path.rstrip("/")
        return f"{raw}/apps" if raw.startswith("/") else ""

    def setup_files(self, cosmos: CosmosClient, hostname: str) -> None:
        if cosmos.upsert_route(files_route(hostname)):
            output.ok("Route /files -> FileBrowser saved")
            self.restart_pause(cosmos)

        secret = self.state.get("filebrowser.client_secret")
        redirect = filebrowser.redirect_uri(hostname)
        current = next(
            (c for c in cosmos.list_openid_clients() if c.get("id") == filebrowser.CLIENT_ID), None
        )
        if not (current and secret and current.get("redirect") == redirect):
            if current:
                cosmos.delete_openid_client(filebrowser.CLIENT_ID)
            secret = cosmos.create_openid_client(filebrowser.CLIENT_ID, [redirect])
            self.state.set("filebrowser.client_secret", secret)
            self.state.set("filebrowser.hostname", hostname)
            output.ok("FileBrowser sign-in client registered with Cosmos")
            cosmos.restart()
            self.restart_pause(cosmos, seconds=4)
            output.ok("Cosmos restarted to load the sign-in client")

        template = (Path(self.settings.config_dir) / "filebrowser" / "config.yaml").read_text(
            encoding="utf-8"
        )
        text = filebrowser.render_config(
            template,
            hostname=hostname,
            client_secret=secret,
            admin_username=self.state.get("setup.admin_user") or "spark-pending",
        )
        changed = filebrowser.write_config(self.settings.volumes_dir, text)
        container = self.docker.find_container("filebrowser")
        if container is None:
            output.warn("FileBrowser is not created yet: docker compose up -d filebrowser")
        elif changed:
            self.docker.restart(container["Id"])
            output.ok("FileBrowser config written; FileBrowser restarted")
        elif container.get("State") != "running":
            self.docker.restart(container["Id"])
            output.ok("FileBrowser was not running; started it")
        else:
            output.ok("FileBrowser config already up to date")

    def restart_pause(self, cosmos: CosmosClient, seconds: float = 2) -> None:
        self.sleep(seconds)
        cosmos.wait_until_up()

    def local_url(self) -> str:
        return f"http://localhost:{self.settings.http_port}"


def files_route(hostname: str) -> dict[str, Any]:
    return {
        "Name": FILES_ROUTE,
        "Description": "FileBrowser (SPARK)",
        "UsePathPrefix": True,
        "PathPrefix": "/files",
        "StripPathPrefix": False,  # FileBrowser itself lives under /files (baseURL)
        "Target": "http://filebrowser:80",
        "Mode": "PROXY",
        # FileBrowser does its own login (OpenID against Cosmos) and serves anonymous share
        # links, so Cosmos must not demand a session here.
        "AuthEnabled": False,
        "AdminOnly": False,
        "SmartShield": {"Enabled": True},
        # FileBrowser builds its OpenID callback URL and cookie flags from this header. Cosmos in
        # HTTP mode would always say http, but TLS ends at Cloudflare (laptop-only names stay http).
        "ExtraHeaders": {"X-Forwarded-Proto": public_url(hostname).split(":")[0]},
    }


def validate_nickname(name: str | None) -> str:
    if not name:
        raise SparkError(
            "First run needs the owner account name.",
            hint="Pass --admin-user <name> or set SPARK_ADMIN_USER.",
        )
    if not NICKNAME.fullmatch(name):
        raise SparkError(
            f"'{name}' is not a valid account name.",
            hint="Use 3 to 32 letters or digits only.",
        )
    return name


def validate_password(password: str) -> None:
    problems = []
    if not 9 <= len(password) <= 128:
        problems.append("9 to 128 characters")
    # ASCII only, like Cosmos's `containsany` sets: É or ٣ would not count there.
    if not any(c in string.ascii_uppercase for c in password):
        problems.append("an uppercase letter")
    if not any(c in string.ascii_lowercase for c in password):
        problems.append("a lowercase letter")
    if not any(c in string.digits for c in password):
        problems.append("a digit")
    if not any(c in PASSWORD_SPECIALS for c in password):
        problems.append(f"one of {PASSWORD_SPECIALS}")
    if problems:
        raise SparkError("The owner password needs " + ", ".join(problems) + ".")


def ask_name() -> str:
    """The owner account name, asked in a terminal on first run; empty without one."""
    if not sys.stdin.isatty():
        return ""
    return input("Owner account name (3-32 letters or digits): ").strip()


def ask_password() -> str:
    if not sys.stdin.isatty():
        raise SparkError(
            "First run needs the owner password.",
            hint="Set SPARK_ADMIN_PASSWORD for this run (docker compose run -e ...), "
            "or run in a terminal to be asked.",
        )
    password = getpass.getpass("Owner password: ")
    if getpass.getpass("Repeat it: ") != password:
        raise SparkError("The passwords do not match.")
    return password
