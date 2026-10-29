import json

import httpx
import pytest

from spark_cli.cosmos import CosmosClient, CosmosError
from spark_cli.errors import SparkError


class FakeCosmos:
    """Records requests and answers from a {(method, path): response} table."""

    def __init__(self, answers: dict):
        self.answers = answers
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        answer = self.answers[(request.method, request.url.path)]
        if isinstance(answer, Exception):
            raise answer
        if callable(answer):
            return answer(request)
        status, body = answer
        return httpx.Response(status, json=body)


def client(answers: dict, token: str = "cosmos_tok") -> tuple[CosmosClient, FakeCosmos]:
    fake = FakeCosmos(answers)
    http = httpx.Client(transport=httpx.MockTransport(fake), base_url="http://cosmos:80")
    return CosmosClient(http, "spark.example.com", token), fake


def ok(data=None):
    return 200, {"status": "OK", "data": data}


def test_requests_carry_host_and_token():
    cosmos, fake = client({("GET", "/cosmos/api/users"): ok([{"nickname": "arun"}])})
    assert cosmos.list_users() == [{"nickname": "arun"}]
    request = fake.requests[0]
    assert request.headers["host"] == "spark.example.com"
    assert request.headers["authorization"] == "Bearer cosmos_tok"


def test_errors_carry_status_code_and_token_hint():
    cosmos, _ = client(
        {
            ("POST", "/cosmos/api/users"): (
                409,
                {"status": "error", "message": "User limit reached", "code": "UC014"},
            ),
            ("GET", "/cosmos/api/config"): (
                401,
                {"status": "error", "message": "User not logged in", "code": "HTTP004"},
            ),
        }
    )
    with pytest.raises(CosmosError) as exc:
        cosmos.create_user("bob")
    assert (exc.value.status, exc.value.code) == (409, "UC014")
    with pytest.raises(CosmosError) as exc:
        cosmos.get_config()
    assert "run spark setup again" in exc.value.hint


def test_unreachable_cosmos_is_a_spark_error():
    cosmos, _ = client({("GET", "/cosmos/api/users"): httpx.ConnectError("refused")})
    with pytest.raises(SparkError, match="Cannot reach Cosmos"):
        cosmos.list_users()


@pytest.mark.parametrize(("status", "new"), [(200, True), (401, False)])
def test_is_new_install_asks_without_a_token(status, new):
    cosmos, fake = client({("GET", "/cosmos/api/status"): (status, {})})
    assert cosmos.is_new_install() is new
    assert "authorization" not in fake.requests[0].headers


def test_wait_until_up_retries_through_a_restart():
    answers = iter([httpx.ConnectError("refused"), httpx.Response(502), httpx.Response(401)])

    def status(request):
        answer = next(answers)
        if isinstance(answer, Exception):
            raise answer
        return answer

    cosmos, fake = client({("GET", "/cosmos/api/status"): status})
    sleeps = []
    cosmos.wait_until_up(timeout=60, interval=2, sleep=sleeps.append, clock=lambda: 0.0)
    assert len(fake.requests) == 3
    assert sleeps == [2, 2]


def test_wait_until_up_gives_up():
    cosmos, _ = client({("GET", "/cosmos/api/status"): httpx.ConnectError("refused")})
    now = iter([0.0, 5.0, 11.0])
    with pytest.raises(SparkError, match="did not come up"):
        cosmos.wait_until_up(timeout=10, sleep=lambda _: None, clock=lambda: next(now))


def test_setup_sends_http_only_setup_and_returns_the_token():
    cosmos, fake = client(
        {("POST", "/cosmos/api/setup"): ok({"adminToken": "cosmos_new", "adminTokenName": "x"})},
        token="",
    )
    token = cosmos.setup(
        nickname="arun", password="Pa55word!x", email="", hostname="spark.example.com"
    )
    assert token == "cosmos_new"
    body = json.loads(fake.requests[0].content)
    assert body["httpsCertificateMode"] == "DISABLED"
    assert body["createAdminToken"] is True
    assert body["hostname"] == "spark.example.com"
    assert "email" not in body


def test_update_config_saves_only_when_something_changed():
    config = {"AutoUpdate": True, "DockerConfig": {"SkipPruneImages": True}}
    saved = []

    def put(request):
        saved.append(json.loads(request.content))
        return httpx.Response(200, json={"status": "OK"})

    cosmos, _ = client(
        {("GET", "/cosmos/api/config"): ok(config), ("PUT", "/cosmos/api/config"): put}
    )

    assert cosmos.update_config(lambda c: c["DockerConfig"].update(SkipPruneImages=True)) is False
    assert saved == []
    assert cosmos.update_config(lambda c: c.update(AutoUpdate=False)) is True
    assert saved == [{"AutoUpdate": False, "DockerConfig": {"SkipPruneImages": True}}]


def test_upsert_route_creates_updates_or_leaves_alone():
    routes = [{"Name": "SPARK Files", "Target": "http://filebrowser:80", "Timeout": 0}]
    cosmos, fake = client(
        {
            ("GET", "/cosmos/api/routes"): ok(routes),
            ("POST", "/cosmos/api/routes"): ok({}),
            ("PUT", "/cosmos/api/routes/SPARK Files"): ok({}),
        }
    )
    assert cosmos.upsert_route({"Name": "SPARK Files", "Target": "http://filebrowser:80"}) is False
    assert cosmos.upsert_route({"Name": "SPARK Files", "Target": "http://fb:80"}) is True
    put = fake.requests[-1]
    assert put.method == "PUT"
    assert json.loads(put.content) == {
        "Name": "SPARK Files",
        "Target": "http://fb:80",
        "Timeout": 0,
    }
    assert cosmos.upsert_route({"Name": "Other", "Target": "http://x"}) is True
    assert fake.requests[-1].method == "POST"


def test_upsert_route_compares_and_merges_nested_values():
    # Cosmos answers with all of SmartShield's fields, not just the ones SPARK sets.
    shield = {"Enabled": True, "PolicyStrictness": 0, "PerUserRequestLimit": 0}
    routes = [{"Name": "SPARK Files", "SmartShield": shield, "ExtraHeaders": {"X-A": "1"}}]
    cosmos, fake = client(
        {
            ("GET", "/cosmos/api/routes"): ok(routes),
            ("PUT", "/cosmos/api/routes/SPARK Files"): ok({}),
        }
    )
    assert cosmos.upsert_route({"Name": "SPARK Files", "SmartShield": {"Enabled": True}}) is False
    assert cosmos.upsert_route({"Name": "SPARK Files", "SmartShield": {"Enabled": False}}) is True
    assert json.loads(fake.requests[-1].content)["SmartShield"] == {**shield, "Enabled": False}


def test_openid_client_create_returns_the_plain_secret():
    cosmos, fake = client(
        {("POST", "/cosmos/api/openid"): ok({"id": "filebrowser", "secret": "plain"})}
    )
    assert cosmos.create_openid_client("filebrowser", ["https://a/cb", "https://b/cb"]) == "plain"
    assert json.loads(fake.requests[0].content) == {
        "id": "filebrowser",
        "redirect": "https://a/cb,https://b/cb",
    }


def test_create_user_sends_role():
    cosmos, fake = client(
        {("POST", "/cosmos/api/users"): ok({"registerKey": "k", "registerKeyExp": "2026-11-05"})}
    )
    assert cosmos.create_user("bob", admin=True)["registerKey"] == "k"
    assert json.loads(fake.requests[0].content) == {"nickname": "bob", "email": "", "role": 2}


def test_restart_survives_cosmos_exiting_before_it_answers():
    cosmos, fake = client({("GET", "/cosmos/api/restart"): httpx.RemoteProtocolError("closed")})
    cosmos.restart()
    assert fake.requests[0].headers["authorization"] == "Bearer cosmos_tok"


def test_restart_reports_a_refusal():
    cosmos, _ = client(
        {("GET", "/cosmos/api/restart"): (403, {"status": "error", "message": "Forbidden"})}
    )
    with pytest.raises(CosmosError):
        cosmos.restart()
