"""FileBrowser Quantum config: rendering config/filebrowser/config.yaml and writing it into the
filebrowser-data volume, where FileBrowser reads it (FILEBROWSER_CONFIG)."""

import contextlib
import os
import re
from pathlib import Path

from spark_cli.errors import SparkError
from spark_cli.settings import public_url

CLIENT_ID = "filebrowser"
PLACEHOLDERS = (
    "SPARK_HOSTNAME",
    "SPARK_PUBLIC_URL",
    "FB_OIDC_CLIENT_SECRET",
    "SPARK_ADMIN_USERNAME",
)
FILEBROWSER_UID = 1000  # the image's user

_PLACEHOLDER = re.compile(r"\$\{([^}]*)\}")
# Values end up inside double-quoted YAML strings, and FileBrowser expands $ references itself.
_FORBIDDEN = ('"', "\\", "$", "\n", "\r")


def redirect_uri(hostname: str) -> str:
    return f"{public_url(hostname)}/files/api/auth/oidc/callback"


def render_config(template: str, *, hostname: str, client_secret: str, admin_username: str) -> str:
    values = {
        "SPARK_HOSTNAME": hostname,
        "SPARK_PUBLIC_URL": public_url(hostname),
        "FB_OIDC_CLIENT_SECRET": client_secret,
        "SPARK_ADMIN_USERNAME": admin_username,
    }
    for name, value in values.items():
        if not value or any(c in value for c in _FORBIDDEN):
            raise SparkError(f"Cannot put {name} into the FileBrowser config: empty or unsafe.")

    def substitute(match: re.Match) -> str:
        name = match.group(1)
        if name not in values:
            raise SparkError(
                f"Unknown placeholder ${{{name}}} in the FileBrowser config template.",
                hint=f"Only {', '.join(PLACEHOLDERS)} are filled in.",
            )
        return values[name]

    return _PLACEHOLDER.sub(substitute, template)


def config_path(volumes_dir: Path) -> Path:
    return Path(volumes_dir) / "filebrowser-data" / "config.yaml"


def write_config(volumes_dir: Path, text: str) -> bool:
    """Atomically write the config (it holds the OpenID client secret: mode 0600, owned by
    FileBrowser's uid when we run as root). True when the content changed.

    Also gives the volume's folder to FileBrowser, which creates its database there. The CLI
    mounts the empty volume before FileBrowser ever does, so Docker makes it root's instead of
    copying the image's folder (owned by uid 1000) into it."""
    path = config_path(volumes_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    _give_to_filebrowser(path.parent)
    if path.is_file() and path.read_text(encoding="utf-8") == text:
        return False
    tmp = path.with_name(f".{path.name}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    os.chmod(tmp, 0o600)
    _give_to_filebrowser(tmp)
    os.replace(tmp, path)
    return True


def _give_to_filebrowser(path: Path) -> None:
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        with contextlib.suppress(OSError):
            os.chown(path, FILEBROWSER_UID, FILEBROWSER_UID)
