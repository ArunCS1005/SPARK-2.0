"""Cloudflare API client: remotely managed tunnels and their DNS records (API v4).

Endpoints as documented at developers.cloudflare.com/api (zero_trust/tunnels/cloudflared,
dns/records, zones) and the "Create a tunnel (API)" guide. The token needs Account > Cloudflare
Tunnel: Edit and Zone > DNS: Edit (Zone > Zone Settings: Edit only for --always-https)."""

from typing import Any

import httpx

from spark_cli.errors import SparkError

API = "https://api.cloudflare.com/client/v4"
TUNNEL_DOMAIN = "cfargotunnel.com"
PERMISSIONS_HINT = (
    "Check CF_API_TOKEN: it needs 'Account > Cloudflare Tunnel: Edit' and 'Zone > DNS: Edit' for "
    "the account in CF_ACCOUNT_ID and the zone in CF_ZONE_ID."
)


class CloudflareError(SparkError):
    def __init__(self, message: str, *, status: int, hint: str | None = None):
        super().__init__(message, hint)
        self.status = status


class CloudflareClient:
    def __init__(self, http: httpx.Client, account_id: str, zone_id: str):
        self.http = http
        self.account = f"/accounts/{account_id}"
        self.zone = f"/zones/{zone_id}"

    @classmethod
    def from_settings(cls, settings) -> "CloudflareClient":
        settings.require_cloudflare()
        http = httpx.Client(
            base_url=API,
            headers={"Authorization": f"Bearer {settings.cf_api_token}"},
            timeout=30,
        )
        return cls(http, settings.cf_account_id, settings.cf_zone_id)

    def _call(self, method: str, path: str, **kwargs: Any) -> Any:
        return self._send(method, path, **kwargs).get("result")

    def _send(self, method: str, path: str, **kwargs: Any) -> dict:
        try:
            response = self.http.request(method, path, **kwargs)
        except httpx.TransportError as exc:
            raise SparkError(
                f"Cannot reach the Cloudflare API ({exc.__class__.__name__}).",
                hint="Check the internet connection.",
            ) from exc
        try:
            body = response.json()
        except ValueError:
            body = {}
        if response.status_code >= 400 or body.get("success") is False:
            errors = "; ".join(
                f"{e.get('message')} ({e.get('code')})" for e in body.get("errors") or []
            )
            raise CloudflareError(
                f"Cloudflare {method} {path} failed: {errors or response.reason_phrase}",
                status=response.status_code,
                hint=PERMISSIONS_HINT if response.status_code in (401, 403) else None,
            )
        return body

    def _all(self, path: str, params: dict[str, Any]) -> list[dict]:
        """Every page of a list endpoint (result_info.total_pages)."""
        items: list[dict] = []
        page = 1
        while True:
            body = self._send("GET", path, params={**params, "page": page, "per_page": 100})
            items += body.get("result") or []
            if page >= int((body.get("result_info") or {}).get("total_pages") or 1):
                return items
            page += 1

    # -- zone --------------------------------------------------------------------------------

    def zone_name(self) -> str:
        return (self._call("GET", self.zone) or {})["name"]

    def always_use_https(self) -> None:
        self._call("PATCH", f"{self.zone}/settings/always_use_https", json={"value": "on"})

    # -- tunnels -----------------------------------------------------------------------------

    def tunnels(self, name: str | None = None) -> list[dict]:
        params: dict[str, Any] = {"is_deleted": "false"}
        if name:
            params["name"] = name
        found = self._all(f"{self.account}/cfd_tunnel", params)
        return [t for t in found if not t.get("deleted_at") and (not name or t["name"] == name)]

    def create_tunnel(self, name: str) -> dict:
        """Remotely managed (its ingress lives at Cloudflare); returns {id, token, ...}."""
        payload = {"name": name, "config_src": "cloudflare"}
        return self._call("POST", f"{self.account}/cfd_tunnel", json=payload) or {}

    def tunnel_token(self, tunnel_id: str) -> str:
        return self._call("GET", f"{self.account}/cfd_tunnel/{tunnel_id}/token")

    def set_ingress(self, tunnel_id: str, hostnames: list[str], service: str) -> None:
        rules: list[dict[str, Any]] = [
            {"hostname": h, "service": service, "originRequest": {}} for h in hostnames
        ]
        rules.append({"service": "http_status:404"})
        path = f"{self.account}/cfd_tunnel/{tunnel_id}/configurations"
        self._call("PUT", path, json={"config": {"ingress": rules}})

    def delete_tunnel(self, tunnel_id: str) -> None:
        """A tunnel must have no active connections to be deleted; removing its connectors first
        disconnects any cloudflared still running with its token."""
        self._call("DELETE", f"{self.account}/cfd_tunnel/{tunnel_id}/connections")
        self._call("DELETE", f"{self.account}/cfd_tunnel/{tunnel_id}")

    # -- DNS ---------------------------------------------------------------------------------

    def records(self, name: str | None = None) -> list[dict]:
        """Records of any type with this exact name (an A record blocks a CNAME too), or every
        CNAME in the zone."""
        params: dict[str, Any] = {"name.exact": name} if name else {"type": "CNAME"}
        return self._all(f"{self.zone}/dns_records", params)

    def create_cname(self, name: str, target: str, comment: str) -> None:
        payload = {
            "type": "CNAME",
            "name": name,
            "content": target,
            "proxied": True,
            "comment": comment,
        }
        self._call("POST", f"{self.zone}/dns_records", json=payload)

    def delete_record(self, record_id: str) -> None:
        self._call("DELETE", f"{self.zone}/dns_records/{record_id}")


def tunnel_target(tunnel_id: str) -> str:
    return f"{tunnel_id}.{TUNNEL_DOMAIN}"
