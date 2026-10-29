"""Minimal Docker Engine API client over the unix socket. Only what SPARK needs: find the project's
containers, restart/stop/start them, read logs, inspect the project network."""

import json
import struct
from typing import Any

import httpx

from spark_cli.errors import SparkError


class DockerClient:
    def __init__(self, http: httpx.Client, project: str = "spark"):
        self.http = http
        self.project = project

    @classmethod
    def from_socket(cls, socket_path: str, project: str = "spark") -> "DockerClient":
        transport = httpx.HTTPTransport(uds=socket_path)
        return cls(httpx.Client(transport=transport, base_url="http://docker", timeout=60), project)

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            response = self.http.request(method, path, **kwargs)
        except httpx.TransportError as exc:
            raise SparkError(
                f"Cannot reach the Docker engine ({exc}).",
                hint="Run the CLI with the Docker socket mounted: "
                "docker compose run --rm spark ...",
            ) from exc
        if response.status_code >= 400 and response.status_code != 304:
            raise SparkError(
                f"Docker API {method} {path} failed: {response.status_code} {response.text}"
            )
        return response

    def ping(self) -> bool:
        try:
            return self.http.get("/_ping").text.strip() == "OK"
        except httpx.TransportError:
            return False

    def version(self) -> dict[str, Any]:
        return self._request("GET", "/version").json()

    def info(self) -> dict[str, Any]:
        return self._request("GET", "/info").json()

    def find_container(self, service: str) -> dict[str, Any] | None:
        """The project's container for a compose service (running or not), or None."""
        filters = {
            "label": [
                f"com.docker.compose.project={self.project}",
                f"com.docker.compose.service={service}",
            ]
        }
        containers = self._request(
            "GET", "/containers/json", params={"all": "1", "filters": json.dumps(filters)}
        ).json()
        # Ignore one-off `docker compose run` containers of the same service.
        containers = [
            c for c in containers if c.get("Labels", {}).get("com.docker.compose.oneoff") != "True"
        ]
        return containers[0] if containers else None

    def require_container(self, service: str) -> dict[str, Any]:
        container = self.find_container(service)
        if container is None:
            raise SparkError(
                f"The '{service}' container does not exist.",
                hint="Start SPARK first: docker compose up -d",
            )
        return container

    def inspect(self, container_id: str) -> dict[str, Any]:
        return self._request("GET", f"/containers/{container_id}/json").json()

    def restart(self, container_id: str, timeout: int = 20) -> None:
        self._request("POST", f"/containers/{container_id}/restart", params={"t": timeout})

    def stop(self, container_id: str, timeout: int = 20) -> None:
        self._request("POST", f"/containers/{container_id}/stop", params={"t": timeout})

    def start(self, container_id: str) -> None:
        self._request("POST", f"/containers/{container_id}/start")

    def logs(self, container_id: str, tail: int = 500) -> str:
        response = self._request(
            "GET",
            f"/containers/{container_id}/logs",
            params={"stdout": "1", "stderr": "1", "tail": str(tail)},
        )
        return demultiplex(response.content)

    def network(self, name: str) -> dict[str, Any] | None:
        response = self.http.get(f"/networks/{name}")
        if response.status_code == 404:
            return None
        if response.status_code >= 400:
            raise SparkError(f"Docker API GET /networks/{name} failed: {response.status_code}")
        return response.json()

    def network_subnets(self, name: str) -> list[str]:
        network = self.network(name) or {}
        configs = (network.get("IPAM") or {}).get("Config") or []
        return [c["Subnet"] for c in configs if c.get("Subnet")]

    def project_network(self, container_id: str) -> tuple[str, dict[str, Any]]:
        """The compose project's network a container is on, with its endpoint settings."""
        networks = self.inspect(container_id)["NetworkSettings"]["Networks"]
        for name, endpoint in networks.items():
            if name == f"{self.project}_default":
                return name, endpoint
        if len(networks) == 1:
            return next(iter(networks.items()))
        raise SparkError(f"Cannot tell which of {sorted(networks)} is the {self.project} network.")

    def add_network_alias(self, container_id: str, alias: str) -> bool:
        """Give a container an extra DNS name on its project network, keeping the names it has
        (compose's service name among them). Aliases cannot be edited in place, so this
        disconnects and reconnects the container. True when it changed anything."""
        network, endpoint = self.project_network(container_id)
        aliases = list(endpoint.get("Aliases") or [])
        if alias in aliases:
            return False
        self._request("POST", f"/networks/{network}/disconnect", json={"Container": container_id})
        self._request(
            "POST",
            f"/networks/{network}/connect",
            json={"Container": container_id, "EndpointConfig": {"Aliases": [*aliases, alias]}},
        )
        return True


def demultiplex(raw: bytes) -> str:
    """Decode Docker's multiplexed log stream (8-byte frame headers when the container has no TTY).
    Falls back to plain text for TTY containers."""
    if len(raw) < 8 or raw[0] not in (0, 1, 2) or raw[1:4] != b"\x00\x00\x00":
        return raw.decode("utf-8", errors="replace")
    chunks: list[bytes] = []
    offset = 0
    while offset + 8 <= len(raw):
        (size,) = struct.unpack(">I", raw[offset + 4 : offset + 8])
        chunks.append(raw[offset + 8 : offset + 8 + size])
        offset += 8 + size
    return b"".join(chunks).decode("utf-8", errors="replace")
