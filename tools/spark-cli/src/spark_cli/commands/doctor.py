"""`spark doctor`: read-only health report. One line per check, OK / WARN / FAIL; exit code 1 when
anything failed. Never changes Docker, Cosmos or a file, so it is safe to run at any time.

Cosmos's settings are read from its config file in the volume rather than its API: the file is what
Cosmos loads at its next start, and it can be read while Cosmos is down."""

import argparse
import json
import os
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from spark_cli import filebrowser, seed
from spark_cli.cosmos import CosmosClient, CosmosError
from spark_cli.docker_api import DockerClient
from spark_cli.errors import SparkError
from spark_cli.settings import Settings, is_laptop_only
from spark_cli.state import State

OK, WARN, FAIL = "ok", "warn", "fail"
PENDING_SECRET = "pending-setup"  # commands/init.py placeholder
LOW_DISK_BYTES = 5 * 1024**3
IMAGES = {"cosmos": "cosmos", "filebrowser": "filebrowser", "tunnel": "cloudflared"}


@dataclass
class Result:
    level: str
    message: str
    hint: str = ""


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("doctor", help="check that everything is healthy (read-only)")
    parser.set_defaults(handler=run)


def run(args: argparse.Namespace, settings: Settings) -> int:
    docker = DockerClient.from_socket(settings.docker_socket, settings.project)
    return Doctor(settings, docker, State(settings.state_dir)).run()


class Doctor:
    def __init__(
        self,
        settings: Settings,
        docker: DockerClient,
        state: State,
        *,
        cosmos_factory: Callable[..., CosmosClient] = CosmosClient.from_settings,
    ):
        self.settings = settings
        self.docker = docker
        self.state = state
        self.cosmos_factory = cosmos_factory
        self.hostname = str(state.get("setup.hostname") or "")
        self.cosmos_config: dict[str, Any] = {}
        self.results: list[Result] = []

    def run(self) -> int:
        for check in (
            self.check_containers,
            self.check_cosmos_safety,
            self.check_cosmos_api,
            self.check_hostname,
            self.check_files_wiring,
            self.check_filebrowser_config,
            self.check_data_folders,
        ):
            try:
                check()
            except SparkError as exc:
                self.add(FAIL, exc.message, exc.hint or "")
        failed = sum(r.level == FAIL for r in self.results)
        warned = sum(r.level == WARN for r in self.results)
        if failed:
            print(f"\n{failed} problem(s), {warned} warning(s).")
            return 1
        print(f"\nHealthy ({warned} warning(s))." if warned else "\nAll checks passed.")
        return 0

    def add(self, level: str, message: str, hint: str = "") -> None:
        self.results.append(Result(level, message, hint))
        print(f"[{level}]{' ' * (5 - len(level))}{message}")
        if hint:
            print(f"       {hint}")

    # -- checks ------------------------------------------------------------------------------

    def check_containers(self) -> None:
        if not self.docker.ping():
            raise SparkError(
                "Cannot reach the Docker engine.",
                hint="Run it through compose: docker compose run --rm spark doctor",
            )
        # Without a hostname there is no sign-in for FileBrowser, and the installer leaves it off.
        files = bool(self.settings.hostname) or self.settings.quick_tunnel
        services = ["cosmos", "filebrowser"] if files else ["cosmos"]
        services += [p for p in ("tunnel", "quick-tunnel") if p in self.settings.compose_profiles]
        for service in services:
            container = self.docker.find_container(service)
            if container is None:
                self.add(FAIL, f"{service}: no container", "Start it: docker compose up -d")
                continue
            status = str(container.get("Status", ""))
            if container.get("State") != "running":
                self.add(
                    FAIL,
                    f"{service}: {status or container.get('State')}",
                    f"See why: docker compose logs {service}",
                )
            elif "unhealthy" in status:
                self.add(
                    FAIL,
                    f"{service}: running but unhealthy",
                    f"See why: docker compose logs {service}",
                )
            else:
                self.add(OK, f"{service}: {status}")
            self.check_version(service, str(container.get("Image", "")))

    def check_version(self, service: str, image: str) -> None:
        pinned = self.settings.versions.get(IMAGES.get(service.replace("quick-", ""), ""), "")
        tag = image.rsplit(":", 1)[1] if ":" in image.rsplit("/", 1)[-1] else ""
        if pinned and tag and not image.startswith("sha256:") and tag != pinned:
            self.add(
                WARN,
                f"{service} runs {image}, but .env pins {pinned}",
                "Recreate it: docker compose up -d",
            )

    def check_cosmos_safety(self) -> None:
        path = seed.config_path(self.settings.volumes_dir)
        config = read_json(path)
        if config is None:
            self.add(
                FAIL,
                f"Cosmos config missing or unreadable: {path}",
                "Cosmos must not start without it. Run: docker compose up -d (init repairs "
                "it), or restore a backup.",
            )
            return
        unsafe = [
            key for key, value in seed.SAFETY_KEYS.items() if lookup(config, key) is not value
        ]
        if unsafe:
            self.add(
                FAIL,
                f"Cosmos safety settings are off: {', '.join(unsafe)}",
                "Cosmos may delete unused images and networks of other projects or update "
                "itself. Fix: docker compose up -d (init puts them back), then spark setup.",
            )
        else:
            self.add(OK, "Cosmos safety settings: pruning and auto-update off")
        if config.get("NewInstall"):
            self.add(
                FAIL,
                "Cosmos is waiting for first-run setup: anyone who reaches it can take it over",
                "Run now: docker compose run --rm spark setup",
            )
        if not seed.last_good_path(self.settings.volumes_dir).is_file():
            self.add(
                WARN,
                "No last-good copy of the Cosmos config",
                "init writes one on the next docker compose up -d",
            )
        self.cosmos_config = config

    def check_cosmos_api(self) -> None:
        token = self.state.get("cosmos.admin_token")
        if not token:
            self.add(
                WARN, "SPARK has no Cosmos admin token", "Run: docker compose run --rm spark setup"
            )
            return
        cosmos = self.cosmos_factory(
            self.settings, token=token, hostname=self.hostname or "localhost"
        )
        try:
            cosmos.status()
        except CosmosError as exc:
            if exc.status in (401, 403):
                self.add(
                    FAIL,
                    "Cosmos rejects SPARK's admin token",
                    "Was the provisioning token deleted in the dashboard? See the "
                    "troubleshooting guide, 'Lost admin token'.",
                )
            else:
                self.add(FAIL, f"Cosmos API: {exc.message}")
            return
        self.add(OK, "Cosmos API answers and accepts SPARK's token")

    def check_hostname(self) -> None:
        configured = str(lookup(self.cosmos_config, "HTTPConfig.Hostname") or "")
        wanted = self.settings.hostname
        if self.settings.quick_tunnel:
            wanted = self.quick_tunnel_hostname() or self.hostname
        if not wanted:
            self.add(
                WARN,
                "No public hostname: Files is not available",
                "Set SPARK_HOSTNAME (or use spark.localhost) and run spark setup.",
            )
            return
        if configured and configured != wanted:
            self.add(
                FAIL,
                f"Cosmos serves {configured}, but the hostname is now {wanted}",
                "Run: docker compose run --rm spark setup",
            )
        else:
            self.add(OK, f"Hostname: {wanted}")
        if is_laptop_only(wanted) and self.settings.http_port != "80":
            self.add(
                FAIL,
                f"{wanted} needs SPARK_HTTP_PORT=80",
                "Sign-in links carry no port. Set it in .env, then docker compose up -d.",
            )

    def quick_tunnel_hostname(self) -> str:
        from spark_cli.commands.setup import QUICK_TUNNEL_URL

        container = self.docker.find_container("quick-tunnel")
        if container is None:
            return ""
        match = QUICK_TUNNEL_URL.search(self.docker.logs(container["Id"], tail=200))
        return match.group(1) if match else ""

    def check_files_wiring(self) -> None:
        config = self.cosmos_config
        if not config or not self.hostname or self.hostname == "localhost":
            return
        routes = lookup(config, "HTTPConfig.ProxyConfig.Routes") or []
        route = next((r for r in routes if r.get("PathPrefix") == "/files"), None)
        if route is None or route.get("Target") != "http://filebrowser:80":
            self.add(FAIL, "Cosmos has no /files route to FileBrowser", "Run: spark setup")
        elif route.get("AuthEnabled"):
            self.add(
                FAIL,
                "The /files route requires a Cosmos login, so share links break",
                "Run: spark setup",
            )
        else:
            self.add(OK, "Route /files -> FileBrowser")
        clients = config.get("OpenIDClients") or []
        client = next((c for c in clients if c.get("id") == filebrowser.CLIENT_ID), None)
        if client is None or client.get("redirect") != filebrowser.redirect_uri(self.hostname):
            self.add(
                FAIL,
                "FileBrowser's sign-in client in Cosmos is missing or outdated",
                "Run: spark setup",
            )
        else:
            self.add(OK, "FileBrowser sign-in client registered")

    def check_filebrowser_config(self) -> None:
        path = filebrowser.config_path(self.settings.volumes_dir)
        if not path.is_file():
            self.add(FAIL, "FileBrowser has no config", "Run: docker compose up -d")
            return
        text = path.read_text(encoding="utf-8")
        mode = path.stat().st_mode & 0o777
        if mode & 0o077:
            self.add(
                WARN,
                f"FileBrowser config is readable by others (mode {mode:o})",
                "It holds the sign-in secret. Run spark setup to rewrite it.",
            )
        owner = path.parent.stat().st_uid
        if hasattr(os, "geteuid") and owner != filebrowser.FILEBROWSER_UID:
            self.add(
                FAIL,
                f"FileBrowser's data folder belongs to uid {owner}, not "
                f"{filebrowser.FILEBROWSER_UID}",
                "Run spark setup to fix its owner.",
            )
        if self.hostname and self.hostname != "localhost":
            if PENDING_SECRET in text:
                self.add(FAIL, "FileBrowser still has the placeholder config", "Run: spark setup")
            elif f'issuerUrl: "http://{self.hostname}"' not in text:
                self.add(
                    FAIL, f"FileBrowser's config is not for {self.hostname}", "Run: spark setup"
                )
            else:
                self.add(OK, "FileBrowser config matches the hostname")

    def check_data_folders(self) -> None:
        data = Path(self.settings.data_dir)
        missing = [name for name in ("users", "shared", "backups") if not (data / name).is_dir()]
        if missing:
            self.add(
                FAIL,
                f"Missing data folders: {', '.join(missing)}",
                "Run: docker compose up -d (init creates them)",
            )
        for name in ("users", "shared"):
            path = data / name
            if path.is_dir() and not writable_by_filebrowser(path):
                self.add(
                    FAIL,
                    f"FileBrowser (uid {filebrowser.FILEBROWSER_UID}) cannot write to "
                    f"SPARK_DATA/{name}",
                    f"Fix: sudo chown {filebrowser.FILEBROWSER_UID} <SPARK_DATA>/{name}",
                )
        if data.is_dir():
            usage = shutil.disk_usage(data)
            free = usage.free / 1024**3
            if usage.free < LOW_DISK_BYTES or usage.free < usage.total * 0.05:
                self.add(WARN, f"Low disk space: {free:.1f} GB free for SPARK_DATA")
            else:
                self.add(OK, f"Disk: {free:.1f} GB free for SPARK_DATA")


def writable_by_filebrowser(path: Path) -> bool:
    st = path.stat()
    if st.st_uid == filebrowser.FILEBROWSER_UID:
        return bool(st.st_mode & 0o200)
    if st.st_gid == filebrowser.FILEBROWSER_UID:
        return bool(st.st_mode & 0o020)
    return bool(st.st_mode & 0o002)


def read_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def lookup(config: dict, dotted: str) -> Any:
    node: Any = config
    for part in dotted.split("."):
        if not isinstance(node, dict):
            return None
        node = node.get(part)
    return node
