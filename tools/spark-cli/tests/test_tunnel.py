import json
from urllib.parse import parse_qs

import httpx
import pytest

from spark_cli.cli import build_parser
from spark_cli.cloudflare import CloudflareClient
from spark_cli.errors import SparkError

ACCOUNT, ZONE = "acc1", "zone1"


class FakeCloudflare:
    """The parts of API v4 that spark tunnel uses, with paging (result_info)."""

    def __init__(self):
        self.tunnels: list[dict] = []
        self.records: list[dict] = []
        self.ingress: dict[str, list] = {}
        self.calls: list[str] = []
        self.always_https = None
        self.page_size = 2  # small, so paging is exercised

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer cf_tok"
        path = request.url.path.removeprefix("/client/v4")
        query = {k: v[0] for k, v in parse_qs(request.url.query.decode()).items()}
        body = json.loads(request.content) if request.content else None
        self.calls.append(f"{request.method} {path}")
        acc, zone = f"/accounts/{ACCOUNT}", f"/zones/{ZONE}"

        if path == zone:
            return ok({"id": ZONE, "name": "example.com"})
        if path == f"{zone}/settings/always_use_https":
            self.always_https = body["value"]
            return ok({"id": "always_use_https", "value": body["value"]})
        if path == f"{acc}/cfd_tunnel" and request.method == "GET":
            found = [t for t in self.tunnels if "name" not in query or t["name"] == query["name"]]
            return self.page([{k: v for k, v in t.items() if k != "token"} for t in found], query)
        if path == f"{acc}/cfd_tunnel" and request.method == "POST":
            assert body["config_src"] == "cloudflare"
            tunnel = {
                "id": f"uuid-{len(self.tunnels) + 1}",
                "name": body["name"],
                "status": "inactive",
                "token": f"token-{len(self.tunnels) + 1}",
                "deleted_at": None,
            }
            self.tunnels.append(tunnel)
            return ok(tunnel)
        if path.endswith("/token"):
            tunnel_id = path.split("/")[-2]
            return ok(next(t["token"] for t in self.tunnels if t["id"] == tunnel_id))
        if path.endswith("/configurations"):
            self.ingress[path.split("/")[-2]] = body["config"]["ingress"]
            return ok({})
        if path.endswith("/connections") and request.method == "DELETE":
            return ok(None)
        if path.startswith(f"{acc}/cfd_tunnel/") and request.method == "DELETE":
            tunnel_id = path.split("/")[-1]
            self.tunnels = [t for t in self.tunnels if t["id"] != tunnel_id]
            return ok({})
        if path == f"{zone}/dns_records" and request.method == "GET":
            found = [
                r
                for r in self.records
                if ("name.exact" not in query or r["name"] == query["name.exact"])
                and ("type" not in query or r["type"] == query["type"])
            ]
            return self.page(found, query)
        if path == f"{zone}/dns_records" and request.method == "POST":
            self.records.append({**body, "id": f"rec-{len(self.records) + 1}"})
            return ok(body)
        if path.startswith(f"{zone}/dns_records/") and request.method == "DELETE":
            record_id = path.split("/")[-1]
            self.records = [r for r in self.records if r["id"] != record_id]
            return ok({"id": record_id})
        raise AssertionError(f"unexpected {request.method} {path}")

    def page(self, items, query):
        page = int(query.get("page", 1))
        size = self.page_size
        total_pages = max(1, -(-len(items) // size))
        chunk = items[(page - 1) * size : page * size]
        info = {"page": page, "per_page": size, "total_pages": total_pages}
        return httpx.Response(200, json={"success": True, "result": chunk, "result_info": info})

    def writes(self):
        return [c for c in self.calls if not c.startswith("GET")]


def ok(result):
    return httpx.Response(
        200, json={"success": True, "errors": [], "messages": [], "result": result}
    )


@pytest.fixture
def cf(monkeypatch):
    fake = FakeCloudflare()

    def from_settings(settings):
        settings.require_cloudflare()
        http = httpx.Client(
            transport=httpx.MockTransport(fake),
            base_url="https://api.cloudflare.com/client/v4",
            headers={"Authorization": f"Bearer {settings.cf_api_token}"},
        )
        return CloudflareClient(http, settings.cf_account_id, settings.cf_zone_id)

    monkeypatch.setattr(CloudflareClient, "from_settings", staticmethod(from_settings))
    return fake


@pytest.fixture
def settings(make_settings):
    return make_settings(cf_api_token="cf_tok", cf_account_id=ACCOUNT, cf_zone_id=ZONE)


def spark(settings, *argv) -> int:
    args = build_parser().parse_args(["tunnel", *argv])
    return args.handler(args, settings)


def test_new_creates_tunnel_ingress_dns_and_prints_token(settings, cf, capsys):
    assert spark(settings, "new", "Bob", "--also", "cloud") == 0

    [tunnel] = cf.tunnels
    assert tunnel["name"] == "spark-bob"
    assert cf.ingress["uuid-1"] == [
        {"hostname": "bob.example.com", "service": "http://cosmos:80", "originRequest": {}},
        {"hostname": "bob-cloud.example.com", "service": "http://cosmos:80", "originRequest": {}},
        {"service": "http_status:404"},
    ]
    assert [(r["name"], r["content"], r["proxied"]) for r in cf.records] == [
        ("bob.example.com", "uuid-1.cfargotunnel.com", True),
        ("bob-cloud.example.com", "uuid-1.cfargotunnel.com", True),
    ]
    out = capsys.readouterr().out
    assert "\ntoken-1\n" in out
    assert "SPARK_HOSTNAME=bob.example.com" in out
    assert "cf_tok" not in out
    assert cf.always_https is None


def test_new_again_reuses_the_tunnel_and_adds_only_whats_missing(settings, cf, capsys):
    spark(settings, "new", "bob")
    cf.calls.clear()
    assert spark(settings, "new", "bob", "--also", "cloud") == 0
    assert len(cf.tunnels) == 1
    assert [r["name"] for r in cf.records] == ["bob.example.com", "bob-cloud.example.com"]
    assert "POST /accounts/acc1/cfd_tunnel" not in cf.writes()
    assert "\ntoken-1\n" in capsys.readouterr().out  # fetched from the token endpoint


def test_new_never_takes_over_someone_elses_dns_name(settings, cf):
    cf.records.append(
        {"id": "r0", "type": "A", "name": "bob.example.com", "content": "203.0.113.5"}
    )
    with pytest.raises(SparkError, match="bob.example.com already has a DNS record"):
        spark(settings, "new", "bob")
    assert cf.writes() == []


def test_new_with_always_https(settings, cf, capsys):
    spark(settings, "new", "bob", "--always-https")
    assert cf.always_https == "on"
    assert "Always Use HTTPS is on for all of example.com" in capsys.readouterr().out


@pytest.mark.parametrize(
    "argv",
    [["new", "bob.smith"], ["new", "bob-"], ["new", "bob", "--also", "a_b"], ["new", "x" * 64]],
)
def test_new_refuses_names_that_are_not_one_dns_label(settings, cf, argv):
    with pytest.raises(SparkError, match="is not a valid"):
        spark(settings, *argv)
    assert cf.calls == []


def test_missing_cloudflare_settings(make_settings, cf):
    with pytest.raises(SparkError, match="CF_API_TOKEN, CF_ACCOUNT_ID, CF_ZONE_ID"):
        spark(make_settings(), "list")


def test_permission_error_explains_the_token(settings, monkeypatch):
    def handler(request):
        error = {"success": False, "errors": [{"code": 10000, "message": "Authentication error"}]}
        return httpx.Response(403, json=error)

    http = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://x")
    client = CloudflareClient(http, ACCOUNT, ZONE)
    with pytest.raises(SparkError, match="Authentication error") as exc:
        client.zone_name()
    assert "Cloudflare Tunnel: Edit" in exc.value.hint


def test_list_shows_only_spark_tunnels_with_their_hostnames(settings, cf, capsys):
    spark(settings, "new", "bob", "--also", "cloud")
    spark(settings, "new", "carol")
    cf.tunnels.append({"id": "other", "name": "homelab", "status": "healthy", "deleted_at": None})
    cf.records.append(
        {"id": "x", "type": "CNAME", "name": "www.example.com", "content": "example.net"}
    )
    capsys.readouterr()

    assert spark(settings, "list") == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].split() == ["TUNNEL", "STATUS", "HOSTNAMES"]
    assert lines[1].split() == [
        "spark-bob",
        "inactive",
        "bob-cloud.example.com,",
        "bob.example.com",
    ]
    assert lines[2].split() == ["spark-carol", "inactive", "carol.example.com"]
    assert len(lines) == 3


def test_remove_deletes_dns_connections_and_tunnel(settings, cf):
    spark(settings, "new", "bob", "--also", "cloud")
    spark(settings, "new", "carol")
    cf.calls.clear()

    assert spark(settings, "remove", "spark-bob", "--yes") == 0
    assert [t["name"] for t in cf.tunnels] == ["spark-carol"]
    assert [r["name"] for r in cf.records] == ["carol.example.com"]
    assert cf.writes()[-2:] == [
        "DELETE /accounts/acc1/cfd_tunnel/uuid-1/connections",
        "DELETE /accounts/acc1/cfd_tunnel/uuid-1",
    ]


def test_remove_needs_confirmation_and_an_existing_tunnel(settings, cf, monkeypatch):
    with pytest.raises(SparkError, match="no tunnel spark-ghost"):
        spark(settings, "remove", "ghost", "--yes")
    spark(settings, "new", "bob")
    cf.calls.clear()
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    with pytest.raises(SparkError, match="without confirmation"):
        spark(settings, "remove", "bob")
    assert cf.writes() == []
