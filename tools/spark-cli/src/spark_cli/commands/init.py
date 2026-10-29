"""`spark init`: run by the one-shot `init` service before Cosmos on every `docker compose up`.
Creates the SPARK_DATA folders and makes sure neither Cosmos nor FileBrowser ever starts without a
config SPARK wrote. No network, no Docker socket; safe to run any number of times."""

import argparse
import contextlib
import os
from pathlib import Path

from spark_cli import filebrowser, output, seed
from spark_cli.errors import SparkError
from spark_cli.settings import Settings

DATA_FOLDERS = ("users", "shared", "media/movies", "media/shows", "media/music", "backups")

# FileBrowser runs as uid 1000: it creates each user's home in users/, and admins write to shared/.
FILEBROWSER_UID = 1000
FILEBROWSER_WRITABLE = ("users", "shared")

# Until `spark setup` renders the real FileBrowser config. Cosmos nicknames are alphanumeric
# (user/create.go), so the placeholder admin name can never match a real account.
PLACEHOLDER_SECRET = "pending-setup"
PLACEHOLDER_ADMIN = "spark-pending"


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "init", help="Prepare data folders and first-start configs (run by the init service)"
    )
    parser.set_defaults(handler=run)


def run(args: argparse.Namespace, settings: Settings) -> int:
    created = create_data_folders(settings.data_dir)
    if created:
        output.ok(f"Created data folders in {settings.data_dir}: {', '.join(created)}")
    else:
        output.ok(f"Data folders present in {settings.data_dir}")

    result, changed = seed.ensure_cosmos_config(
        settings.volumes_dir,
        settings.config_dir,
        hostname=settings.hostname,
        country=settings.server_country,
    )
    if result == "created":
        output.ok("Cosmos config created from the seed (new install)")
    elif result == "repaired":
        output.warn(f"Cosmos config had unsafe values; put back {', '.join(changed)}")
    elif result == "restored":
        output.warn("Cosmos config was missing or unreadable; restored SPARK's last good copy")
    else:
        output.ok("Cosmos config present; safety settings ok")

    if write_filebrowser_placeholder(settings):
        output.ok("FileBrowser placeholder config written (spark setup replaces it)")
    else:
        output.ok("FileBrowser config present (left unchanged)")
    return 0


def create_data_folders(data_dir: Path) -> list[str]:
    """Create the SPARK_DATA tree; returns the folders that were missing. Existing folders and their
    owners are never touched."""
    created = []
    for name in DATA_FOLDERS:
        path = Path(data_dir) / name
        if path.is_dir():
            continue
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise SparkError(
                f"Cannot create {path}: {exc}",
                hint="SPARK_DATA in .env must point to a writable folder.",
            ) from exc
        if name in FILEBROWSER_WRITABLE:
            _give_to_filebrowser(path)
        created.append(name)
    return created


def write_filebrowser_placeholder(settings: Settings) -> bool:
    """Without a config FileBrowser falls back to its built-in one, which creates a password user
    `admin`. Writes a placeholder only when no config exists; True when written."""
    path = filebrowser.config_path(settings.volumes_dir)
    if path.exists():
        if not path.is_file():
            raise SparkError(f"{path} exists but is not a file.", hint="Remove it and run again.")
        return False
    template_path = Path(settings.config_dir) / "filebrowser" / "config.yaml"
    try:
        template = template_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SparkError(
            f"Cannot read the FileBrowser config template {template_path}: {exc}",
            hint="The repository's config/ folder must be mounted at /app/config.",
        ) from exc
    text = filebrowser.render_config(
        template,
        hostname=settings.hostname or "localhost",
        client_secret=PLACEHOLDER_SECRET,
        admin_username=PLACEHOLDER_ADMIN,
    )
    filebrowser.write_config(settings.volumes_dir, text)
    return True


def _give_to_filebrowser(path: Path) -> None:
    # Only matters on Linux, where folders created by this root container would be root-owned.
    # Windows bind mounts ignore ownership.
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        with contextlib.suppress(OSError):
            os.chown(path, FILEBROWSER_UID, FILEBROWSER_UID)
