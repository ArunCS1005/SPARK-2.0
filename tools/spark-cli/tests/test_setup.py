import argparse
import base64
import copy
import hashlib
import hmac
import itertools
import json
import os
import stat

import httpx
import pytest

from spark_cli import filebrowser
from spark_cli.commands.setup import Setup
from spark_cli.cosmos import CosmosClient
from spark_cli.errors import SparkError
from spark_cli.filebrowser import FileBrowserClient
from spark_cli.state import State

TOKEN = "cosmos_admin_token"
PASSWORD = "Pa55word!x"
REDIRECT = "https://spark.example.com/files/api/auth/oidc/callback"


class FakeCosmosServer:
    """In-memory Cosmos v0.23.4, only the parts `spark setup` uses. Like the real one it checks
    the Host header once set up, needs the admin token for admin calls, answers routes with all
    of their fields, and its sign-in server only knows the OpenID clients that existed at its
    last restart (cosmos.py has the source references)."""

    def __init__(self, *, new_install: bool = True):
        self.new_install = new_install
        self.config = {
            "AutoUpdate": True,
            "ServerCountry": "",
            "RequireMFA": False,
            "DockerConfig": {"SkipPruneNetwork": False, "SkipPruneImages": False},
            "HTTPConfig": {"Hostname": "0.0.0.0", "AllowHTTPLocalIPAccess": False},
        }
        self.routes: list[dict] = []
        self.clients: list[dict] = []
        self.signin_clients: list[dict] = []  # what the sign-in server loaded at its start
        self.restarts = 0
        self.setup_payload: dict | None = None
        self.writes: list[str] = []
        self.secrets = (f"oidc-secret-{n}" for n in itertools.count(1))

    def __call__(self, request: httpx.Request) -> httpx.Response:
        method, path = request.method, request.url.path.removeprefix("/cosmos/api")
        if path == "/status":
            return reply(200 if self.new_install else 401, {})
        if path == "/setup":
            return self.setup(json.loads(request.content))
        http = self.config["HTTPConfig"]
        allowed = {http["Hostname"]} | ({"localhost"} if http["AllowHTTPLocalIPAccess"] else set())
        if request.headers["host"] not in allowed:
            return reply(400, {"status": "error", "message": "Invalid hostname"})
        if request.headers.get("authorization") != f"Bearer {TOKEN}":
            return reply(401, {"status": "error", "message": "User not logged in"})
        if method != "GET":
            self.writes.append(f"{method} {path}")
        body = json.loads(request.content) if request.content else None

        if path == "/config":
            if method == "PUT":
                self.config = body
            return ok(copy.deepcopy(self.config))
        if path == "/routes":
            if method == "POST":
                self.routes.append(stored_route(body))
            return ok(copy.deepcopy(self.routes))
        if path.startswith("/routes/") and method == "PUT":
            name = path.removeprefix("/routes/")
            self.routes = [stored_route(body) if r["Name"] == name else r for r in self.routes]
            return ok(body)
        if path == "/openid" and method == "GET":
            return ok([{**c, "secret": ""} for c in self.clients])
        if path == "/openid" and method == "POST":
            secret = next(self.secrets)
            self.clients.append({"id": body["id"], "redirect": body["redirect"], "public": False})
            return ok({**body, "secret": secret})
        if path.startswith("/openid/") and method == "DELETE":
            client_id = path.removeprefix("/openid/")
            self.clients = [c for c in self.clients if c["id"] != client_id]
            return ok(None)
        if path == "/restart":
            self.restarts += 1
            self.signin_clients = copy.deepcopy(self.clients)
            return ok(None)
        raise AssertionError(f"unexpected request {method} {path}")

    def setup(self, payload: dict) -> httpx.Response:
        if not self.new_install:
            return reply(403, {"status": "error", "message": "Server is already configured"})
        self.setup_payload = payload
        self.writes.append("POST /setup")
        self.new_install = False
        self.config["HTTPConfig"]["Hostname"] = payload["hostname"]
        self.config["HTTPConfig"]["AllowHTTPLocalIPAccess"] = payload["allowHTTPLocalIPAccess"]
        return ok({"adminToken": TOKEN, "adminTokenName": "provisioning-token"})


def stored_route(route: dict) -> dict:
    shield = {"Enabled": False, "PolicyStrictness": 0, "PerUserTimeBudget": 0}
    return {
        "UseHost": False,
        "Host": "",
        **route,
        "SmartShield": {**shield, **route["SmartShield"]},
    }


def reply(status: int, body) -> httpx.Response:
    return httpx.Response(status, json=body)


def ok(data) -> httpx.Response:
    return reply(200, {"status": "OK", "data": data})


class FakeFileBrowser:
    """FileBrowser 2.0.2's users API as SPARK uses it, behind httpx.MockTransport. New users get
    userDefaults rights (no admin, no share) and Home becomes /<name>, like state.CreateUser;
    PATCH changes only the fields in `which`. Every call must carry a valid service token."""

    def __init__(self):
        self.users: dict[str, dict] = {}
        self.writes: list[str] = []
        self.up = True
        self.secret: str | None = None  # set by the test helper from SPARK's state

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/files/health":
            return httpx.Response(200 if self.up else 503)
        self.check_token(request.headers.get("x-jwt-assertion", ""))
        name = request.url.params.get("username")
        if request.method == "GET":
            user = self.users.get(name)
            return httpx.Response(200, json=user) if user else httpx.Response(404)
        body = json.loads(request.content)
        self.writes.append(f"{request.method} {name or body['data']['username']}")
        if request.method == "POST":
            data = body["data"]
            scopes = [
                {**s, "scope": f"/{data['username']}" if s["name"] == "Home" else s["scope"]}
                for s in data["scopes"]
            ]
            self.users[data["username"]] = {
                "username": data["username"],
                "loginMethod": data["loginMethod"],
                "permissions": {"admin": False, "share": False, "api": False, "realtime": False},
                "scopes": scopes,
            }
            return httpx.Response(201)
        if request.method == "PATCH":
            for field in body["which"]:
                self.users[name][field] = body["data"][field]
            return httpx.Response(204)
        raise AssertionError(f"unexpected {request.method} {request.url}")

    def check_token(self, token: str) -> None:
        head, claims, signature = token.split(".")
        expected = hmac.new(self.secret.encode(), f"{head}.{claims}".encode(), hashlib.sha256)
        assert signature == base64.urlsafe_b64encode(expected.digest()).rstrip(b"=").decode()
        payload = json.loads(base64.urlsafe_b64decode(claims + "=="))
        assert payload["sub"] == "spark-cli" and payload["groups"] == ["spark-automation"]


class FakeDocker:
    def __init__(self, *, logs: str = "", filebrowser: bool = True):
        services = ["cosmos", "quick-tunnel"] + (["filebrowser"] if filebrowser else [])
        self.containers = {s: {"Id": f"{s}-id", "State": "running"} for s in services}
        # compose gives cosmos its service name and ${SPARK_HOSTNAME} as aliases
        self.aliases = {"cosmos-id": ["cosmos", "spark.example.com"]}
        self.log_text = logs
        self.added_aliases: list[str] = []
        self.restarted: list[str] = []
        self.files = FakeFileBrowser()  # the API inside the filebrowser container

    def find_container(self, service):
        return self.containers.get(service)

    def require_container(self, service):
        if service not in self.containers:
            raise SparkError(f"The '{service}' container does not exist.")
        return self.containers[service]

    def project_network(self, container_id):
        return "spark_default", {"Aliases": list(self.aliases.get(container_id, []))}

    def network_subnets(self, name):
        assert name == "spark_default"
        return ["172.30.0.0/16"]

    def add_network_alias(self, container_id, alias):
        if alias in self.aliases.setdefault(container_id, []):
            return False
        self.aliases[container_id].append(alias)
        self.added_aliases.append(alias)
        return True

    def logs(self, container_id, tail=500):
        assert container_id == "quick-tunnel-id"
        return self.log_text

    def restart(self, container_id, timeout=20):
        self.restarted.append(container_id)


@pytest.fixture
def server():
    return FakeCosmosServer()


@pytest.fixture
def docker():
    return FakeDocker()


@pytest.fixture
def setup_settings(make_settings):
    def factory(**overrides):
        values = {
            "admin_user": "Arun",
            "admin_password": PASSWORD,
            "data_host_path": "/srv/spark",
            "compose_profiles": ("tunnel",),
        }
        return make_settings(**{**values, **overrides})

    return factory


def run_setup(settings, docker, server, **arg_overrides) -> int:
    def cosmos_factory(settings, token="", hostname=None):
        http = httpx.Client(transport=httpx.MockTransport(server), base_url="http://cosmos:80")
        return CosmosClient(http, hostname or settings.hostname or "localhost", token)

    def filebrowser_factory(settings, secret):
        docker.files.secret = secret
        http = httpx.Client(
            transport=httpx.MockTransport(docker.files), base_url="http://filebrowser:80"
        )
        return FileBrowserClient(http, secret)

    args = {"admin_user": None, "admin_email": None, "require_mfa": False, **arg_overrides}
    setup = Setup(
        settings,
        docker,
        State(settings.state_dir),
        cosmos_factory=cosmos_factory,
        filebrowser_factory=filebrowser_factory,
        sleep=lambda _: None,
    )
    return setup.run(argparse.Namespace(**args))


def fb_config(settings) -> str:
    return filebrowser.config_path(settings.volumes_dir).read_text(encoding="utf-8")


def test_first_run_sets_up_cosmos_and_files(setup_settings, docker, server, capsys):
    settings = setup_settings()
    assert run_setup(settings, docker, server) == 0

    payload = server.setup_payload
    assert payload["nickname"] == "Arun"
    assert payload["password"] == PASSWORD
    assert payload["hostname"] == "spark.example.com"
    assert payload["httpsCertificateMode"] == "DISABLED"
    assert payload["createAdminToken"] is True

    config = server.config
    assert config["AutoUpdate"] is False
    assert config["DockerConfig"] == {
        "SkipPruneNetwork": True,
        "SkipPruneImages": True,
        "DefaultDataPath": "/srv/spark/apps",
    }
    assert config["HTTPConfig"] == {
        "Hostname": "spark.example.com",
        "AllowHTTPLocalIPAccess": True,
        "TrustedProxies": ["172.30.0.0/16"],
    }
    assert config["ServerCountry"] == "IN"
    assert config["RequireMFA"] is False

    [route] = server.routes
    assert route["PathPrefix"] == "/files"
    assert route["Target"] == "http://filebrowser:80"
    assert route["AuthEnabled"] is False
    assert route["ExtraHeaders"] == {"X-Forwarded-Proto": "https"}
    assert server.clients == [{"id": "filebrowser", "redirect": REDIRECT, "public": False}]
    assert server.signin_clients == server.clients  # Cosmos restarted to load it
    assert server.restarts == 1

    text = fb_config(settings)
    assert 'clientSecret: "oidc-secret-1"' in text
    assert 'adminUsername: "arun"' in text  # Cosmos stores nicknames lowercased
    assert 'issuerUrl: "http://spark.example.com"' in text
    mode = os.stat(filebrowser.config_path(settings.volumes_dir)).st_mode
    assert stat.S_IMODE(mode) == 0o600
    assert docker.restarted == ["filebrowser-id"]
    assert docker.added_aliases == []  # compose already set the alias

    state = State(settings.state_dir)
    assert state.get("cosmos.admin_token") == TOKEN
    assert state.get("setup.admin_user") == "arun"
    assert state.get("setup.hostname") == "spark.example.com"
    assert state.get("filebrowser.client_secret") == "oidc-secret-1"

    out = capsys.readouterr()
    for secret in (PASSWORD, TOKEN, "oidc-secret-1"):
        assert secret not in out.out + out.err


def test_rerun_writes_and_restarts_nothing(setup_settings, docker, server, capsys):
    settings = setup_settings()
    run_setup(settings, docker, server)
    config_before = fb_config(settings)
    server.writes.clear()
    docker.restarted.clear()
    capsys.readouterr()

    assert run_setup(settings, docker, server) == 0

    assert server.writes == []
    assert server.restarts == 1  # still only the first run's
    assert docker.restarted == []
    assert docker.added_aliases == []
    assert fb_config(settings) == config_before
    out = capsys.readouterr().out
    assert "Cosmos settings already up to date" in out
    assert "FileBrowser config already up to date" in out


def test_local_only_mode_sets_up_cosmos_without_files(setup_settings, docker, server, capsys):
    settings = setup_settings(hostname="", compose_profiles=())
    assert run_setup(settings, docker, server) == 0

    assert server.setup_payload["hostname"] == "localhost"
    assert server.config["HTTPConfig"]["Hostname"] == "localhost"
    assert server.config["DockerConfig"]["SkipPruneImages"] is True
    assert server.routes == []
    assert server.clients == []
    assert not filebrowser.config_path(settings.volumes_dir).exists()
    assert docker.restarted == []
    assert State(settings.state_dir).get("setup.hostname") == "localhost"
    assert "No public hostname" in capsys.readouterr().out

    server.writes.clear()
    assert run_setup(settings, docker, server) == 0
    assert server.writes == []


def test_local_only_install_moves_to_a_hostname_later(setup_settings, docker, server):
    run_setup(setup_settings(hostname="", compose_profiles=()), docker, server)
    settings = setup_settings()
    assert run_setup(settings, docker, server) == 0

    assert server.config["HTTPConfig"]["Hostname"] == "spark.example.com"
    assert server.clients == [{"id": "filebrowser", "redirect": REDIRECT, "public": False}]
    assert server.signin_clients == server.clients
    assert "oidc-secret-1" in fb_config(settings)


def test_quick_tunnel_takes_hostname_from_logs_and_adds_alias(setup_settings, server):
    docker = FakeDocker(logs="INF |  https://lucky-cat-42.trycloudflare.com  |\n")
    settings = setup_settings(hostname="", compose_profiles=("quick-tunnel",))
    assert run_setup(settings, docker, server) == 0

    assert server.setup_payload["hostname"] == "lucky-cat-42.trycloudflare.com"
    assert docker.added_aliases == ["lucky-cat-42.trycloudflare.com"]
    assert server.clients[0]["redirect"] == (
        "https://lucky-cat-42.trycloudflare.com/files/api/auth/oidc/callback"
    )
    assert 'issuerUrl: "http://lucky-cat-42.trycloudflare.com"' in fb_config(settings)

    server.writes.clear()
    docker.restarted.clear()
    assert run_setup(settings, docker, server) == 0
    assert server.writes == []
    assert docker.restarted == []
    assert docker.added_aliases == ["lucky-cat-42.trycloudflare.com"]


def test_quick_tunnel_without_url_stops_before_cosmos(setup_settings, server):
    docker = FakeDocker(logs="INF Starting tunnel\n")
    settings = setup_settings(hostname="", compose_profiles=("quick-tunnel",))
    with pytest.raises(SparkError, match="has not printed its URL"):
        run_setup(settings, docker, server)
    assert server.setup_payload is None


def test_hostname_change_replaces_openid_client_and_config(setup_settings, docker, server):
    run_setup(setup_settings(), docker, server)
    settings = setup_settings(hostname="new.example.org")
    server.writes.clear()
    docker.restarted.clear()

    assert run_setup(settings, docker, server) == 0

    assert server.config["HTTPConfig"]["Hostname"] == "new.example.org"
    assert docker.added_aliases == ["new.example.org"]
    assert server.clients == [
        {
            "id": "filebrowser",
            "redirect": "https://new.example.org/files/api/auth/oidc/callback",
            "public": False,
        }
    ]
    assert "DELETE /openid/filebrowser" in server.writes
    assert server.signin_clients == server.clients
    text = fb_config(settings)
    assert 'clientSecret: "oidc-secret-2"' in text
    assert 'externalUrl: "https://new.example.org/files"' in text
    assert docker.restarted == ["filebrowser-id"]
    assert State(settings.state_dir).get("filebrowser.hostname") == "new.example.org"


def test_lost_client_secret_registers_a_new_client(setup_settings, docker, server):
    settings = setup_settings()
    run_setup(settings, docker, server)
    State(settings.state_dir).delete("filebrowser.client_secret")

    run_setup(settings, docker, server)
    assert len(server.clients) == 1
    assert 'clientSecret: "oidc-secret-2"' in fb_config(settings)


@pytest.mark.parametrize(
    ("user", "password", "message"),
    [
        ("", PASSWORD, "needs the owner account name"),
        ("ab", PASSWORD, "not a valid account name"),
        ("arun-k", PASSWORD, "not a valid account name"),
        ("arun", "Sh0rt!pw", "9 to 128 characters"),
        ("arun", "nouppercase1!", "an uppercase letter"),
        ("arun", "NOLOWERCASE1!", "a lowercase letter"),
        ("arun", "NoDigitsHere!", "a digit"),
        ("arun", "NoSpecials123", "one of"),
        ("arun", "ÉÉÉÉééééé1!", "an uppercase letter"),
    ],
)
def test_first_run_checks_owner_before_calling_cosmos(
    setup_settings, docker, server, user, password, message
):
    settings = setup_settings(admin_user=user, admin_password=password)
    with pytest.raises(SparkError, match=message):
        run_setup(settings, docker, server)
    assert server.setup_payload is None
    assert server.writes == []


def test_admin_user_flag_wins_over_environment(setup_settings, docker, server):
    run_setup(setup_settings(admin_user="fromenv"), docker, server, admin_user="fromflag")
    assert server.setup_payload["nickname"] == "fromflag"


def test_first_run_without_password_and_terminal_explains(
    setup_settings, docker, server, monkeypatch
):
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    with pytest.raises(SparkError, match="needs the owner password") as exc:
        run_setup(setup_settings(admin_password=""), docker, server)
    assert "SPARK_ADMIN_PASSWORD" in exc.value.hint
    assert server.setup_payload is None


def test_set_up_cosmos_without_token_is_refused(setup_settings, docker):
    server = FakeCosmosServer(new_install=False)
    with pytest.raises(SparkError, match="no admin token") as exc:
        run_setup(setup_settings(), docker, server)
    assert "Lost admin token" in exc.value.hint
    assert server.writes == []


def test_require_mfa_turns_it_on(setup_settings, docker, server):
    run_setup(setup_settings(), docker, server, require_mfa=True)
    assert server.config["RequireMFA"] is True


def test_relative_data_path_leaves_app_storage_alone(setup_settings, docker, server):
    run_setup(setup_settings(data_host_path="./data"), docker, server)
    assert "DefaultDataPath" not in server.config["DockerConfig"]


def test_missing_filebrowser_container_is_a_warning(setup_settings, server, capsys):
    docker = FakeDocker(filebrowser=False)
    settings = setup_settings()
    assert run_setup(settings, docker, server) == 0
    assert docker.restarted == []
    assert filebrowser.config_path(settings.volumes_dir).is_file()
    assert "FileBrowser is not created yet" in capsys.readouterr().out


def test_stopped_filebrowser_is_started_without_config_change(
    setup_settings, docker, server, capsys
):
    settings = setup_settings()
    run_setup(settings, docker, server)
    docker.restarted.clear()
    docker.containers["filebrowser"]["State"] = "exited"

    run_setup(settings, docker, server)
    assert docker.restarted == ["filebrowser-id"]
    assert "FileBrowser was not running; started it" in capsys.readouterr().out


def test_missing_cosmos_container_is_explained(setup_settings, server):
    docker = FakeDocker()
    del docker.containers["cosmos"]
    with pytest.raises(SparkError, match="'cosmos' container does not exist"):
        run_setup(setup_settings(), docker, server)


def test_laptop_only_hostname_uses_plain_http(setup_settings, docker, server, capsys):
    settings = setup_settings(hostname="spark.localhost", http_port="80")
    assert run_setup(settings, docker, server) == 0

    assert server.routes[0]["ExtraHeaders"] == {"X-Forwarded-Proto": "http"}
    assert server.clients[0]["redirect"] == "http://spark.localhost/files/api/auth/oidc/callback"
    text = fb_config(settings)
    assert 'externalUrl: "http://spark.localhost/files"' in text
    assert 'issuerUrl: "http://spark.localhost"' in text
    assert docker.added_aliases == ["spark.localhost"]
    assert "Open:    http://spark.localhost" in capsys.readouterr().out


def test_moving_from_laptop_only_to_a_domain_switches_to_https(setup_settings, docker, server):
    run_setup(setup_settings(hostname="spark.localhost", http_port="80"), docker, server)
    settings = setup_settings()
    run_setup(settings, docker, server)

    assert server.routes[0]["ExtraHeaders"] == {"X-Forwarded-Proto": "https"}
    assert server.clients[0]["redirect"] == REDIRECT
    assert 'externalUrl: "https://spark.example.com/files"' in fb_config(settings)


@pytest.mark.parametrize(
    ("hostname", "port", "message"),
    [
        ("spark.localhost", "8080", "only works with SPARK_HTTP_PORT=80"),
        ("localhost", "80", "cannot be localhost itself"),
    ],
)
def test_laptop_only_hostname_checks_happen_before_any_change(
    setup_settings, docker, server, hostname, port, message
):
    with pytest.raises(SparkError, match=message):
        run_setup(setup_settings(hostname=hostname, http_port=port), docker, server)
    assert server.setup_payload is None
    assert docker.added_aliases == []


def test_first_run_asks_for_the_owner_name_in_a_terminal(
    setup_settings, docker, server, monkeypatch
):
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt: "Owner1")
    run_setup(setup_settings(admin_user=""), docker, server)
    assert server.setup_payload["nickname"] == "Owner1"


FULL = {"view": True, "download": True, "create": True, "modify": True, "delete": True}


def test_owner_gets_shared_write_access_and_share_links_before_first_sign_in(
    setup_settings, docker, server, capsys
):
    settings = setup_settings()
    run_setup(settings, docker, server)

    owner = docker.files.users["arun"]
    assert owner["loginMethod"] == "oidc"  # the owner's first Cosmos sign-in reuses it
    assert owner["permissions"] == {"admin": True, "share": True, "api": True, "realtime": False}
    assert [(s["name"], s["scope"]) for s in owner["scopes"]] == [
        ("Home", "/arun"),
        ("Shared", "/"),
        ("SharedAdmin", "/"),
    ]
    assert owner["scopes"][2]["permissions"] == FULL
    out = capsys.readouterr().out
    assert "'arun' can write to Shared (as SharedAdmin) and create share links" in out
    secret = State(settings.state_dir).get("filebrowser.automation_secret")
    assert len(secret) >= 40
    assert f'secret: "{secret}"' in fb_config(settings)
    assert secret not in out


def test_owner_who_already_signed_in_gets_the_missing_rights_only(setup_settings, docker, server):
    docker.files.users["arun"] = {
        "username": "arun",
        "loginMethod": "oidc",
        "permissions": {"admin": True, "share": False, "api": False, "realtime": False},
        "scopes": [
            {"name": "Home", "scope": "/arun", "permissions": FULL},
            {"name": "Shared", "scope": "/", "permissions": FULL},
        ],
    }
    run_setup(setup_settings(), docker, server)
    owner = docker.files.users["arun"]
    assert owner["permissions"]["share"] is True
    assert [s["name"] for s in owner["scopes"]] == ["Home", "Shared", "SharedAdmin"]
    assert docker.files.writes == ["PATCH arun", "PATCH arun"]


def test_owner_rights_are_not_rewritten_on_re_runs(setup_settings, docker, server, capsys):
    settings = setup_settings()
    run_setup(settings, docker, server)
    docker.files.writes.clear()
    capsys.readouterr()

    run_setup(settings, docker, server)
    assert docker.files.writes == []
    assert "'arun' already has Shared write access" in capsys.readouterr().out


def test_guests_are_never_touched(setup_settings, docker, server):
    docker.files.users["bob"] = {"username": "bob", "permissions": {}, "scopes": []}
    run_setup(setup_settings(), docker, server)
    assert docker.files.users["bob"] == {"username": "bob", "permissions": {}, "scopes": []}


def test_unanswered_filebrowser_is_a_warning(setup_settings, docker, server, capsys):
    docker.files.up = False
    assert run_setup(setup_settings(), docker, server) == 0
    assert "owner's Files rights were not checked" in capsys.readouterr().out
    assert docker.files.users == {}
