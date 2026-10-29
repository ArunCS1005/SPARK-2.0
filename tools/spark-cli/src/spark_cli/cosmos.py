"""Cosmos Cloud API client (targets v0.23.4; paths and payloads verified in its Go source).

Every request carries `Host: <hostname>`: once set up, Cosmos rejects hostnames it does not serve.
Admin calls use the API token from first-run setup (`Authorization: Bearer cosmos_...`)."""

import copy
import time
from collections.abc import Callable
from typing import Any

import httpx

from spark_cli.errors import SparkError

API = "/cosmos/api"
ROLE_USER = 1
ROLE_ADMIN = 2


class CosmosError(SparkError):
    """An error answer from Cosmos: HTTP status plus Cosmos's own code (e.g. UC014)."""

    def __init__(self, message: str, *, status: int, code: str = "", hint: str | None = None):
        super().__init__(message, hint)
        self.status = status
        self.code = code


class CosmosClient:
    def __init__(self, http: httpx.Client, hostname: str, token: str = ""):
        self.http = http
        self.hostname = hostname
        self.token = token

    @classmethod
    def from_settings(
        cls, settings, token: str = "", hostname: str | None = None
    ) -> "CosmosClient":
        http = httpx.Client(base_url=settings.cosmos_url, timeout=60)
        return cls(http, hostname or settings.hostname or "localhost", token)

    def with_token(self, token: str) -> "CosmosClient":
        return CosmosClient(self.http, self.hostname, token)

    # -- transport ---------------------------------------------------------------------------

    def _send(self, method: str, path: str, *, auth: bool = True, **kwargs: Any) -> httpx.Response:
        headers = {"Host": self.hostname}
        if auth and self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        try:
            return self.http.request(method, path, headers=headers, **kwargs)
        except httpx.TransportError as exc:
            raise SparkError(
                f"Cannot reach Cosmos at {self.http.base_url} ({exc.__class__.__name__}).",
                hint="Is it running? docker compose ps",
            ) from exc

    def _call(self, method: str, path: str, *, auth: bool = True, **kwargs: Any) -> Any:
        response = self._send(method, API + path, auth=auth, **kwargs)
        try:
            body = response.json()
        except ValueError:
            body = {}
        if response.status_code >= 400 or body.get("status") == "error":
            message = body.get("message") or response.reason_phrase
            code = str(body.get("code") or "")
            hint = None
            if response.status_code in (401, 403):
                hint = "Cosmos rejected SPARK's admin token; run spark setup again."
            raise CosmosError(
                f"Cosmos {method} {path} failed: {message} ({response.status_code} {code})".strip(),
                status=response.status_code,
                code=code,
                hint=hint,
            )
        return body.get("data")

    # -- status and setup --------------------------------------------------------------------

    def status(self) -> dict:
        return self._call("GET", "/status") or {}

    def is_new_install(self) -> bool:
        """Cosmos answers /status without a login only while it is waiting for first-run setup."""
        response = self._send("GET", API + "/status", auth=False)
        if response.status_code == 200:
            return True
        if response.status_code == 401:
            return False
        raise CosmosError(
            f"Unexpected answer from Cosmos /status: {response.status_code}",
            status=response.status_code,
        )

    def wait_until_up(
        self,
        timeout: float = 120.0,
        interval: float = 2.0,
        *,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Wait for Cosmos's API to answer (it restarts after setup and some config changes)."""
        deadline = clock() + timeout
        while True:
            try:
                code = self._send("GET", API + "/status", auth=False).status_code
                if code in (200, 401):
                    return
            except SparkError:
                pass
            if clock() >= deadline:
                raise SparkError(
                    f"Cosmos did not come up within {int(timeout)} s.",
                    hint="Check its logs: docker compose logs cosmos",
                )
            sleep(interval)

    def setup(self, *, nickname: str, password: str, email: str, hostname: str) -> str:
        """First-run setup in HTTP-only mode (TLS ends at Cloudflare). Cosmos exits right after
        answering and comes back through its restart policy. Returns the admin API token."""
        payload = {
            "mongodbMode": "",
            "hostname": hostname,
            "httpsCertificateMode": "DISABLED",
            "allowHTTPLocalIPAccess": True,
            "nickname": nickname,
            "password": password,
            "createAdminToken": True,
        }
        if email:
            payload["email"] = email
        data = self._call("POST", "/setup", auth=False, json=payload) or {}
        token = data.get("adminToken")
        if not token:
            raise SparkError("Cosmos's setup did not return an admin token.")
        return token

    # -- config ------------------------------------------------------------------------------

    def get_config(self) -> dict:
        return self._call("GET", "/config") or {}

    def update_config(self, mutate: Callable[[dict], None]) -> bool:
        """Read the config, apply `mutate`, and save it only if something changed. Cosmos blanks
        its private keys on read and keeps its own copy of the auth keys, API tokens and OpenID
        clients on save (configapi/set.go); the self-signed TLS key it regenerates by itself, as
        when the dashboard saves settings. Saving soft-restarts Cosmos's HTTP server."""
        current = self.get_config()
        updated = copy.deepcopy(current)
        mutate(updated)
        if updated == current:
            return False
        self._call("PUT", "/config", json=updated)
        return True

    # -- routes ------------------------------------------------------------------------------

    def list_routes(self) -> list[dict]:
        return self._call("GET", "/routes") or []

    def upsert_route(self, route: dict) -> bool:
        """Create the route, or update the one with the same Name. Only the keys given are
        compared, nested ones included: Cosmos answers with every field of the route (all eight
        of SmartShield's, for one), and its defaults for the rest do not count as changes."""
        existing = next((r for r in self.list_routes() if r.get("Name") == route["Name"]), None)
        if existing is None:
            self._call("POST", "/routes", json=route)
            return True
        if _contains(existing, route):
            return False
        self._call("PUT", f"/routes/{route['Name']}", json=_merged(existing, route))
        return True

    # -- OpenID clients ----------------------------------------------------------------------

    def list_openid_clients(self) -> list[dict]:
        return self._call("GET", "/openid") or []

    def create_openid_client(self, client_id: str, redirect_uris: list[str]) -> str:
        """Returns the plaintext secret; Cosmos only keeps its bcrypt hash."""
        data = self._call(
            "POST", "/openid", json={"id": client_id, "redirect": ",".join(redirect_uris)}
        )
        secret = (data or {}).get("secret")
        if not secret:
            raise SparkError(f"Cosmos did not return a secret for OpenID client '{client_id}'.")
        return secret

    def delete_openid_client(self, client_id: str) -> None:
        self._call("DELETE", f"/openid/{client_id}")

    def restart(self) -> None:
        """Restart Cosmos: it exits and Docker's restart policy brings it back. Needed after
        changing OpenID clients: /openid saves them, but the sign-in server loads its client list
        only when the web server starts (authorizationserver.Init, configapi/restart.go)."""
        try:
            self._call("GET", "/restart")
        except CosmosError:
            raise
        except SparkError:
            pass  # Cosmos may exit before its answer arrives

    # -- users -------------------------------------------------------------------------------

    def list_users(self) -> list[dict]:
        return self._call("GET", "/users") or []

    def create_user(self, nickname: str, *, email: str = "", admin: bool = False) -> dict:
        """Returns {"registerKey", "registerKeyExp"} for the invite link."""
        payload = {"nickname": nickname, "email": email, "role": ROLE_ADMIN if admin else ROLE_USER}
        return self._call("POST", "/users", json=payload) or {}

    def delete_user(self, nickname: str) -> None:
        self._call("DELETE", f"/users/{nickname}")


def _contains(current: Any, wanted: Any) -> bool:
    """True when every key in `wanted` has the same value in `current`, recursing into dicts."""
    if isinstance(wanted, dict):
        return isinstance(current, dict) and all(
            _contains(current.get(key), value) for key, value in wanted.items()
        )
    return current == wanted


def _merged(current: dict, wanted: dict) -> dict:
    result = dict(current)
    for key, value in wanted.items():
        old = result.get(key)
        both_dicts = isinstance(value, dict) and isinstance(old, dict)
        result[key] = _merged(old, value) if both_dicts else value
    return result
