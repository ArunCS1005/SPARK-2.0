"""`spark backup` / `spark restore`: SPARK's settings and accounts, not people's files.

An archive holds the `cosmos-config` and `filebrowser-data` volumes and SPARK's state (admin
token, sign-in secret), plus `manifest.json`. Files in SPARK_DATA (users/, shared/, media/) are not
included: copy that folder with your usual backup tool. Cosmos and FileBrowser are stopped while
their files are read or replaced, and started again afterwards even when something fails."""

import argparse
import io
import json
import os
import shutil
import sys
import tarfile
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from spark_cli import __version__, output, seed
from spark_cli.docker_api import DockerClient
from spark_cli.errors import SparkError
from spark_cli.settings import Settings

FORMAT = 1
MANIFEST = "manifest.json"
# Order matters when starting again: FileBrowser needs Cosmos's sign-in discovery.
SERVICES = ("cosmos", "filebrowser")


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("backup", help="save SPARK's settings and accounts to a file")
    parser.add_argument("--output", help="folder for the archive (default SPARK_DATA/backups)")
    parser.set_defaults(handler=run_backup)

    restore = subparsers.add_parser("restore", help="put back settings from a backup archive")
    restore.add_argument(
        "archive", help="path inside SPARK_DATA, e.g. /data/backups/spark-….tar.gz"
    )
    restore.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    restore.set_defaults(handler=run_restore)


def sources(settings: Settings) -> dict[str, Path]:
    """Top-level name in the archive -> folder in the CLI container."""
    volumes = Path(settings.volumes_dir)
    return {
        "cosmos-config": volumes / "cosmos-config",
        "filebrowser-data": volumes / "filebrowser-data",
        "state": Path(settings.state_dir),
    }


def run_backup(args: argparse.Namespace, settings: Settings) -> int:
    docker = DockerClient.from_socket(settings.docker_socket, settings.project)
    folder = Path(args.output) if args.output else Path(settings.data_dir) / "backups"
    with stopped(docker):
        path = create_archive(settings, folder)
    output.ok(f"Backup written: {path} ({path.stat().st_size / 1024**2:.1f} MB)")
    output.info("It holds passwords and keys: keep it private. People's files in SPARK_DATA")
    output.info("are not in it; back that folder up separately.")
    return 0


def run_restore(args: argparse.Namespace, settings: Settings) -> int:
    archive = Path(args.archive)
    manifest = check_archive(archive)
    pinned = settings.versions.get("cosmos")
    saved = manifest.get("versions", {}).get("cosmos")
    output.info(
        f"Backup from {manifest.get('created', '?')}, hostname "
        f"{manifest.get('hostname') or '-'}, Cosmos {saved or '?'}."
    )
    if pinned and saved and pinned != saved:
        output.warn(f"It was made with Cosmos {saved}; this install runs {pinned}.")
    if not args.yes and not confirm("Replace the current settings and accounts with it?"):
        output.info("Nothing restored.")
        return 1

    docker = DockerClient.from_socket(settings.docker_socket, settings.project)
    with stopped(docker):
        safety = create_archive(settings, Path(settings.data_dir) / "backups", "before-restore")
        output.ok(f"Current settings saved first: {safety}")
        try:
            restore_archive(settings, archive)
        except OSError as exc:
            raise SparkError(
                f"Restore stopped half-way: {exc}",
                hint=f"Put the previous settings back: spark restore {safety} --yes",
            ) from exc
    output.ok("Restored. Check with: docker compose run --rm spark doctor")
    return 0


@contextmanager
def stopped(docker: DockerClient) -> Iterator[None]:
    """Stop the running SPARK services; start exactly those again on the way out."""
    if not docker.ping():
        raise SparkError(
            "Cannot reach the Docker engine.",
            hint="Run it through compose: docker compose run --rm spark ...",
        )
    running = [
        c for c in (docker.find_container(s) for s in SERVICES) if c and c["State"] == "running"
    ]
    for container in reversed(running):
        docker.stop(container["Id"])
    try:
        yield
    finally:
        for container in running:
            docker.start(container["Id"])
        if running:
            output.info("Cosmos and FileBrowser started again.")


def create_archive(settings: Settings, folder: Path, label: str = "") -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = folder / f"spark-{label + '-' if label else ''}{stamp}.tar.gz"
    tmp = folder / f".{path.name}.tmp"
    manifest = {
        "format": FORMAT,
        "created": datetime.now(UTC).isoformat(timespec="seconds"),
        "spark": __version__,
        "hostname": settings.hostname,
        "versions": dict(settings.versions),
        "contents": sorted(sources(settings)),
    }
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "wb") as fh, tarfile.open(fileobj=fh, mode="w:gz") as tar:
            data = json.dumps(manifest, indent=2).encode()
            info = tarfile.TarInfo(MANIFEST)
            info.size, info.mode, info.mtime = len(data), 0o600, int(datetime.now(UTC).timestamp())
            tar.addfile(info, io.BytesIO(data))
            for name, source in sources(settings).items():
                if source.is_dir():
                    # Skip our own temporary files; everything else is copied as it is.
                    tar.add(
                        source,
                        arcname=name,
                        filter=lambda m: None if m.name.endswith(".tmp") else m,
                    )
        # The CLI runs as root; let whoever owns SPARK_DATA copy the archive elsewhere.
        if Path(settings.data_dir).is_dir():
            same_owner(Path(settings.data_dir), tmp)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return path


def check_archive(archive: Path) -> dict[str, Any]:
    """Refuse anything that is not exactly what `spark backup` writes, before touching a thing:
    regular files and folders under the known top-level names; no links, devices, absolute
    paths or '..'."""
    if not archive.is_file():
        raise SparkError(
            f"No such file: {archive}",
            hint="Use the path inside the CLI container, e.g. /data/backups/<name>.tar.gz",
        )
    allowed = {"cosmos-config", "filebrowser-data", "state"}
    manifest = None
    try:
        with tarfile.open(archive, mode="r:gz") as tar:
            for member in tar:
                path = PurePosixPath(member.name)
                bad = (
                    path.is_absolute()
                    or ".." in path.parts
                    or not (member.isfile() or member.isdir())
                    or (member.name != MANIFEST and path.parts[0] not in allowed)
                )
                if bad:
                    raise SparkError(
                        f"{archive.name} contains '{member.name}', which a SPARK backup never "
                        "has. Nothing was restored."
                    )
                if member.name == MANIFEST:
                    manifest = json.load(tar.extractfile(member))
    except (tarfile.TarError, OSError, EOFError, ValueError) as exc:
        raise SparkError(f"{archive.name} is not a readable SPARK backup: {exc}") from exc
    if not isinstance(manifest, dict) or manifest.get("format") != FORMAT:
        raise SparkError(f"{archive.name} is not a SPARK backup (no manifest of format {FORMAT}).")
    return manifest


def restore_archive(settings: Settings, archive: Path) -> None:
    """Unpack into a staging folder next to the backups, then replace each folder's contents.
    Cosmos is started directly afterwards (not through init), so its safety keys are put back
    here first."""
    targets = sources(settings)
    with tempfile.TemporaryDirectory(dir=archive.parent, prefix=".restore-") as staging:
        with tarfile.open(archive, mode="r:gz") as tar:
            # "tar" keeps numeric owners (FileBrowser's files are uid 1000) and still refuses
            # absolute paths and anything outside the staging folder; check_archive did the rest.
            tar.extractall(staging, filter="tar", numeric_owner=True)
        for name, target in targets.items():
            unpacked = Path(staging) / name
            if not unpacked.is_dir():
                continue
            target.mkdir(parents=True, exist_ok=True)
            for child in target.iterdir():
                if child.is_dir() and not child.is_symlink():
                    shutil.rmtree(child)
                else:
                    child.unlink()
            # Each volume is its own filesystem, so this is a copy; keep owners and modes.
            for child in unpacked.iterdir():
                copy_with_owner(child, target / child.name)
            shutil.copystat(unpacked, target)
            same_owner(unpacked, target)

    changed = seed.repair_safety_keys(settings.volumes_dir)
    if changed:
        output.warn(f"The backup had unsafe Cosmos settings; put back {', '.join(changed)}")


def copy_with_owner(source: Path, target: Path) -> None:
    if source.is_dir():
        target.mkdir()
        for child in source.iterdir():
            copy_with_owner(child, target / child.name)
        shutil.copystat(source, target)
    else:
        shutil.copy2(source, target)
    same_owner(source, target)


def same_owner(source: Path, target: Path) -> None:
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        stat = source.stat()
        os.chown(target, stat.st_uid, stat.st_gid)


def confirm(question: str) -> bool:
    if not sys.stdin.isatty():
        raise SparkError(
            "Not restoring without confirmation.",
            hint="Run it in a terminal to be asked, or add --yes.",
        )
    try:
        answer = input(f"{question} [y/N] ")
    except EOFError:
        return False
    return answer.strip().lower() in ("y", "yes")
