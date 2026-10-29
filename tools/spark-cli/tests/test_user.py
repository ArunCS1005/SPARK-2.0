import json
import sqlite3
from datetime import UTC, datetime

import httpx
import pytest

from spark_cli.cli import build_parser
from spark_cli.commands import user
from spark_cli.cosmos import CosmosClient
from spark_cli.errors import SparkError
from spark_cli.state import State

HOST = "spark.example.com"
FAR_FUTURE = "2099-01-01T00:00:00Z"


def account(nickname, role=1, registered=True, **extra):
    return {
        "nickname": nickname,
        "role": role,
        "registeredAt": "2026-10-01T10:00:00Z" if registered else "0001-01-01T00:00:00Z",
        "registerKeyExp": FAR_FUTURE if not registered else "0001-01-01T00:00:00Z",
        "createdAt": "2026-10-01T09:00:00Z",
        **extra,
    }


class FakeCosmosUsers:
    """Cosmos v0.23.4 /users and /status (user/create.go, system.go)."""

    def __init__(self, users=None, licence=5):
        self.users = users if users is not None else [account("arun", role=2)]
        self.licence = licence
        self.host = HOST
        self.full = False  # answer every create with UC014, whatever /status says
        self.created: list[dict] = []
        self.deleted: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["host"] == self.host
        assert request.headers["authorization"] == "Bearer cosmos_tok"
        path = request.url.path.removeprefix("/cosmos/api")
        if path == "/status":
            return ok({"LicenceNumber": self.licence})
        if path == "/users" and request.method == "GET":
            return ok(self.users)
        if path == "/users" and request.method == "POST":
            body = json.loads(request.content)
            if self.full or len(self.users) >= self.licence:
                error = {"status": "error", "message": "User limit reached", "code": "UC014"}
                return httpx.Response(409, json=error)
            self.created.append(body)
            self.users.append(account(body["nickname"], body["role"], registered=False))
            return ok({"registerKey": "k3yABC", "registerKeyExp": "2026-11-05T10:00:00Z"})
        if path.startswith("/users/") and request.method == "DELETE":
            self.deleted.append(path.removeprefix("/users/"))
            return ok(None)
        raise AssertionError(f"unexpected {request.method} {path}")


def ok(data):
    return httpx.Response(200, json={"status": "OK", "data": data})


@pytest.fixture
def cosmos(monkeypatch):
    fake = FakeCosmosUsers()

    def from_settings(settings, token="", hostname=None):
        http = httpx.Client(transport=httpx.MockTransport(fake), base_url="http://cosmos:80")
        return CosmosClient(http, hostname, token)

    monkeypatch.setattr(CosmosClient, "from_settings", staticmethod(from_settings))
    return fake


@pytest.fixture
def settings(make_settings):
    settings = make_settings()
    state = State(settings.state_dir)
    state.set("cosmos.admin_token", "cosmos_tok")
    state.set("setup.hostname", HOST)
    state.set("setup.admin_user", "arun")
    return settings


def spark(settings, *argv) -> int:
    args = build_parser().parse_args(["user", *argv])
    return args.handler(args, settings)


def test_add_prints_a_private_invite_link(settings, cosmos, capsys):
    assert spark(settings, "add", "Bob", "--email", "bob@example.com") == 0
    assert cosmos.created == [{"nickname": "bob", "email": "bob@example.com", "role": 1}]
    out = capsys.readouterr().out
    assert "https://spark.example.com/cosmos-ui/register?t=2&nickname=bob&key=k3yABC" in out
    assert "2 of 5 accounts used" in out
    assert "expires on 2026-11-05" in out


def test_add_admin(settings, cosmos):
    spark(settings, "add", "carol", "--admin")
    assert cosmos.created[0]["role"] == 2


def test_invite_link_is_plain_http_for_a_laptop_only_install(make_settings, cosmos, capsys):
    settings = make_settings(hostname="spark.localhost")
    State(settings.state_dir).set("setup.hostname", "spark.localhost")
    State(settings.state_dir).set("cosmos.admin_token", "cosmos_tok")
    cosmos.host = "spark.localhost"
    spark(settings, "add", "bob")
    assert "http://spark.localhost/cosmos-ui/register?" in capsys.readouterr().out


def test_add_refuses_an_existing_name_in_any_case(settings, cosmos):
    with pytest.raises(SparkError, match="already exists"):
        spark(settings, "add", "ARUN")
    assert cosmos.created == []


def test_add_stops_at_the_account_limit_before_asking_cosmos(settings, cosmos):
    cosmos.users += [account(f"u{n}xx", registered=n % 2 == 0) for n in range(4)]  # pending count
    with pytest.raises(SparkError, match="free tier allows 5 accounts") as exc:
        spark(settings, "add", "bob")
    assert "spark user remove" in exc.value.hint
    assert cosmos.created == []


def test_add_explains_cosmoss_own_limit_answer(settings, cosmos):
    cosmos.users += [account(f"u{n}xx") for n in range(4)]
    cosmos.licence = 9  # /status says more seats than Cosmos then allows
    cosmos.full = True
    with pytest.raises(SparkError, match="licence allows 9 accounts"):
        spark(settings, "add", "bob")


@pytest.mark.parametrize(
    ("name", "message"),
    [("ab", "not a valid"), ("bob-k", "not a valid"), ("Anonymous", "reserved")],
)
def test_add_checks_the_name_first(settings, cosmos, name, message):
    with pytest.raises(SparkError, match=message):
        spark(settings, "add", name)
    assert cosmos.created == []


def test_add_checks_the_email(settings, cosmos):
    with pytest.raises(SparkError, match="not a valid email"):
        spark(settings, "add", "bob", "--email", "bob@")


def test_commands_need_setup_first(make_settings, cosmos):
    with pytest.raises(SparkError, match="not set up yet"):
        spark(make_settings(), "list")


def test_changed_hostname_asks_for_setup_first(settings, cosmos):
    moved = type(settings)(**{**settings.__dict__, "hostname": "new.example.org"})
    with pytest.raises(SparkError, match="was set up for spark.example.com") as exc:
        spark(moved, "add", "bob")
    assert "spark setup" in exc.value.hint


def test_quick_tunnel_uses_the_hostname_setup_found(make_settings, cosmos, capsys):
    settings = make_settings(hostname="", compose_profiles=("quick-tunnel",))
    state = State(settings.state_dir)
    state.set("setup.hostname", HOST)
    state.set("cosmos.admin_token", "cosmos_tok")
    spark(settings, "add", "bob")
    assert f"https://{HOST}/cosmos-ui/register" in capsys.readouterr().out


def make_auth_db(path, rows):
    # Columns as in Cosmos's utils/store_schema.go
    with sqlite3.connect(path) as db:
        db.execute(
            "CREATE TABLE users (nickname TEXT PRIMARY KEY, mfa_key TEXT NOT NULL DEFAULT '',"
            " was_2fa_verified INTEGER NOT NULL DEFAULT 0)"
        )
        db.executemany("INSERT INTO users VALUES (?, ?, ?)", rows)


def test_list_shows_status_and_mfa(settings, cosmos, capsys):
    cosmos.users += [
        account("bob", registered=False),
        account("dave", registered=False, registerKeyExp="2020-01-01T00:00:00Z"),
    ]
    db = settings.volumes_dir / "cosmos-config" / "auth.db"
    db.parent.mkdir(parents=True)
    make_auth_db(db, [("arun", "SECRET", 1), ("bob", "SECRET", 0), ("dave", "", 0)])

    assert spark(settings, "list") == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].split() == ["NAME", "ROLE", "STATUS", "MFA", "CREATED"]
    assert lines[1].split() == ["arun", "admin", "registered", "on", "2026-10-01"]
    assert lines[2].split() == ["bob", "user", "invite", "pending", "off", "2026-10-01"]
    assert lines[3].split() == ["dave", "user", "invite", "expired", "off", "2026-10-01"]
    assert "3 of 5 accounts used" in "\n".join(lines)


def test_list_without_readable_database_shows_unknown_mfa(settings, cosmos, capsys):
    db = settings.volumes_dir / "cosmos-config" / "auth.db"
    db.parent.mkdir(parents=True)
    db.write_text("not a database")
    spark(settings, "list")
    out = capsys.readouterr().out
    assert "arun  admin  registered  ?" in out
    assert "MFA '?'" in out


def test_remove_with_yes(settings, cosmos, capsys):
    cosmos.users.append(account("bob"))
    assert spark(settings, "remove", "Bob", "--yes") == 0
    assert cosmos.deleted == ["bob"]
    assert "SPARK_DATA/users/bob" in capsys.readouterr().out


def test_remove_asks_and_respects_no(settings, cosmos, monkeypatch):
    cosmos.users.append(account("bob"))
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _: "n")
    assert spark(settings, "remove", "bob") == 1
    assert cosmos.deleted == []


def test_remove_without_terminal_needs_yes(settings, cosmos, monkeypatch):
    cosmos.users.append(account("bob"))
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    with pytest.raises(SparkError, match="without confirmation"):
        spark(settings, "remove", "bob")
    assert cosmos.deleted == []


def test_remove_protects_the_owner_and_the_last_admin(settings, cosmos):
    with pytest.raises(SparkError, match="owner account"):
        spark(settings, "remove", "arun", "--yes")
    cosmos.users.append(account("eve", role=2))
    State(settings.state_dir).set("setup.admin_user", "someoneelse")
    cosmos.users = [u for u in cosmos.users if u["nickname"] != "arun"]
    with pytest.raises(SparkError, match="last admin"):
        spark(settings, "remove", "eve", "--yes")
    assert cosmos.deleted == []


def test_remove_unknown_account(settings, cosmos):
    with pytest.raises(SparkError, match="no account named 'ghost'"):
        spark(settings, "remove", "ghost", "--yes")


def test_account_status_uses_the_dashboards_rules():
    now = datetime(2026, 10, 29, tzinfo=UTC)
    assert user.account_status(account("a"), now) == "registered"
    assert user.account_status(account("a", registered=False), now) == "invite pending"
    expired = account("a", registered=False, registerKeyExp="2026-10-01T00:00:00Z")
    assert user.account_status(expired, now) == "invite expired"
