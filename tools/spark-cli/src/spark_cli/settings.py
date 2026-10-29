"""Settings come from the environment (.env via `env_file`). This is the only module that reads
os.environ, so everything else can be tested with plain Settings objects."""

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from spark_cli.errors import SparkError


def is_laptop_only(hostname: str) -> bool:
    """`*.localhost` names reach this laptop only: browsers send them to 127.0.0.1 by themselves
    (RFC 6761), and inside Docker the cosmos network alias answers for them."""
    return hostname == "localhost" or hostname.endswith(".localhost")


def public_url(hostname: str) -> str:
    """How browsers reach SPARK: HTTPS through Cloudflare, or plain HTTP for a laptop-only name."""
    return f"{'http' if is_laptop_only(hostname) else 'https'}://{hostname}"


@dataclass(frozen=True)
class Settings:
    # Public access
    hostname: str = ""
    http_port: str = "8080"
    http_bind: str = "127.0.0.1"
    tunnel_token_set: bool = False
    compose_profiles: tuple[str, ...] = ()
    server_country: str = ""
    timezone: str = "UTC"

    # Owner-only Cloudflare automation
    cf_api_token: str = ""
    cf_account_id: str = ""
    cf_zone_id: str = ""

    # SPARK_DATA as the host sees it (D:/SPARK-data, /srv/spark); /data inside the CLI container
    data_host_path: str = ""

    # Only present while running `spark setup`
    admin_user: str = ""
    admin_password: str = field(default="", repr=False)
    admin_email: str = ""

    # Pinned versions (reported by `spark doctor`)
    versions: Mapping[str, str] = field(default_factory=dict)

    # Wiring inside the CLI container (overridable for development)
    project: str = "spark"
    cosmos_url: str = "http://cosmos:80"
    docker_socket: str = "/var/run/docker.sock"
    state_dir: Path = Path("/state")
    data_dir: Path = Path("/data")
    volumes_dir: Path = Path("/volumes")
    config_dir: Path = Path("/app/config")

    @property
    def quick_tunnel(self) -> bool:
        return "quick-tunnel" in self.compose_profiles

    @property
    def public_url(self) -> str:
        return public_url(self.require_hostname())

    def require_hostname(self) -> str:
        if not self.hostname:
            raise SparkError(
                "SPARK_HOSTNAME is not set.",
                hint="Set SPARK_HOSTNAME in .env (e.g. spark.yourdomain.com), "
                "or use the quick-tunnel profile.",
            )
        return self.hostname

    def require_cloudflare(self) -> None:
        missing = [
            name
            for name, value in (
                ("CF_API_TOKEN", self.cf_api_token),
                ("CF_ACCOUNT_ID", self.cf_account_id),
                ("CF_ZONE_ID", self.cf_zone_id),
            )
            if not value
        ]
        if missing:
            raise SparkError(
                f"Missing Cloudflare settings: {', '.join(missing)}.",
                hint="Add them to .env. The API token needs 'Account > Cloudflare Tunnel: Edit' "
                "and 'Zone > DNS: Edit'.",
            )

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Settings":
        env = os.environ if env is None else env

        def get(name: str, default: str = "") -> str:
            return env.get(name, default).strip()

        profiles = tuple(p.strip() for p in get("COMPOSE_PROFILES").split(",") if p.strip())
        versions = {
            key[: -len("_VERSION")].lower(): value.strip()
            for key, value in env.items()
            if key.endswith("_VERSION") and value.strip()
        }
        return cls(
            hostname=get("SPARK_HOSTNAME").lower(),
            http_port=get("SPARK_HTTP_PORT", "8080"),
            http_bind=get("SPARK_HTTP_BIND", "127.0.0.1"),
            tunnel_token_set=bool(get("TUNNEL_TOKEN")),
            compose_profiles=profiles,
            server_country=get("COSMOS_SERVER_COUNTRY").upper(),
            timezone=get("TZ", "UTC"),
            cf_api_token=get("CF_API_TOKEN"),
            cf_account_id=get("CF_ACCOUNT_ID"),
            cf_zone_id=get("CF_ZONE_ID"),
            data_host_path=get("SPARK_DATA"),
            admin_user=get("SPARK_ADMIN_USER"),
            admin_password=env.get("SPARK_ADMIN_PASSWORD", ""),
            admin_email=get("SPARK_ADMIN_EMAIL"),
            versions=versions,
            project=get("COMPOSE_PROJECT_NAME", "spark") or "spark",
            cosmos_url=get("SPARK_CLI_COSMOS_URL", "http://cosmos:80"),
            docker_socket=get("SPARK_CLI_DOCKER_SOCKET", "/var/run/docker.sock"),
            state_dir=Path(get("SPARK_CLI_STATE_DIR", "/state")),
            data_dir=Path(get("SPARK_CLI_DATA_DIR", "/data")),
            volumes_dir=Path(get("SPARK_CLI_VOLUMES_DIR", "/volumes")),
            config_dir=Path(get("SPARK_CLI_CONFIG_DIR", "/app/config")),
        )
