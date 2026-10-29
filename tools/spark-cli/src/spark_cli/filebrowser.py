"""FileBrowser Quantum: rendering config/filebrowser/config.yaml into the filebrowser-data volume
(FILEBROWSER_CONFIG), and a small admin API client.

The client signs in through FileBrowser's JWT method (auth.methods.jwt in the template): an HS256
token with `sub` = SERVICE_USER and `groups` = [AUTOMATION_GROUP] makes FileBrowser create or reuse
that admin user (internal/web/auth.go SetupJwtUser, internal/auth/jwt.go VerifyExternalJWT)."""

import base64
import contextlib
import hashlib
import hmac
import json
import os
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from spark_cli.errors import SparkError
from spark_cli.settings import public_url

CLIENT_ID = "filebrowser"
PLACEHOLDERS = (
    "SPARK_HOSTNAME",
    "SPARK_PUBLIC_URL",
    "FB_OIDC_CLIENT_SECRET",
    "SPARK_ADMIN_USERNAME",
    "FB_AUTOMATION_SECRET",
)
FILEBROWSER_UID = 1000  # the image's user
SERVICE_USER = "spark-cli"  # not a possible Cosmos name (letters and digits only)
AUTOMATION_GROUP = "spark-automation"
TOKEN_SECONDS = 300

_PLACEHOLDER = re.compile(r"\$\{([^}]*)\}")
# Values end up inside double-quoted YAML strings, and FileBrowser expands $ references itself.
_FORBIDDEN = ('"', "\\", "$", "\n", "\r")


def redirect_uri(hostname: str) -> str:
    return f"{public_url(hostname)}/files/api/auth/oidc/callback"


def render_config(
    template: str,
    *,
    hostname: str,
    client_secret: str,
    admin_username: str,
    automation_secret: str,
) -> str:
    values = {
        "SPARK_HOSTNAME": hostname,
        "SPARK_PUBLIC_URL": public_url(hostname),
        "FB_OIDC_CLIENT_SECRET": client_secret,
        "SPARK_ADMIN_USERNAME": admin_username,
        "FB_AUTOMATION_SECRET": automation_secret,
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


def automation_token(secret: str, now: float | None = None) -> str:
    """A short-lived HS256 JWT for the service user (standard library only)."""
    issued = int(time.time() if now is None else now)
    claims = {
        "sub": SERVICE_USER,
        "groups": [AUTOMATION_GROUP],
        "iat": issued,
        "exp": issued + TOKEN_SECONDS,
    }
    head = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    body = _b64(json.dumps(claims, separators=(",", ":")).encode())
    signature = hmac.new(secret.encode(), f"{head}.{body}".encode(), hashlib.sha256).digest()
    return f"{head}.{body}.{_b64(signature)}"


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


class FileBrowserClient:
    """Admin calls to FileBrowser inside the project network (no Cosmos in between)."""

    def __init__(self, http: httpx.Client, secret: str):
        self.http = http
        self.secret = secret

    @classmethod
    def from_settings(cls, settings, secret: str) -> "FileBrowserClient":
        return cls(httpx.Client(base_url=settings.filebrowser_url, timeout=30), secret)

    def _call(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        headers = {"X-JWT-Assertion": automation_token(self.secret)}
        try:
            response = self.http.request(method, path, headers=headers, **kwargs)
        except httpx.TransportError as exc:
            raise SparkError(
                f"Cannot reach FileBrowser ({exc.__class__.__name__}).",
                hint="Is it running? docker compose ps filebrowser",
            ) from exc
        if response.status_code >= 400 and response.status_code != 404:
            raise SparkError(
                f"FileBrowser {method} {path} failed: {response.status_code} {response.text[:200]}"
            )
        return response

    def wait_until_up(
        self, attempts: int = 45, *, sleep: Callable[[float], None] = time.sleep
    ) -> bool:
        """True once /files/health answers; False after `attempts` tries 2 s apart."""
        for attempt in range(attempts):
            try:
                if self.http.get("/files/health").status_code == 200:
                    return True
            except httpx.TransportError:
                pass
            if attempt + 1 < attempts:
                sleep(2)
        return False

    def get_user(self, username: str) -> dict | None:
        response = self._call("GET", "/files/api/users", params={"username": username})
        return None if response.status_code == 404 else response.json()

    def create_user(self, username: str, *, login_method: str, scopes: list[dict]) -> None:
        """FileBrowser applies userDefaults to new users (permissions included), so rights are
        set afterwards with update_user (state.CreateUser -> settings.ApplyUserDefaultsFrom)."""
        data = {"username": username, "loginMethod": login_method, "scopes": scopes}
        self._call("POST", "/files/api/users", json={"which": [], "data": data})

    def update_user(self, username: str, fields: dict[str, Any]) -> None:
        payload = {"which": sorted(fields), "data": fields}
        self._call("PATCH", "/files/api/users", params={"username": username}, json=payload)
