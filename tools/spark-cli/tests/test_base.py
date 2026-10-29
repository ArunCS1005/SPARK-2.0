import struct

import httpx
import pytest

from spark_cli.cli import main
from spark_cli.docker_api import DockerClient, demultiplex
from spark_cli.errors import SparkError
from spark_cli.settings import Settings, public_url
from spark_cli.state import State


def test_settings_from_env_parses_profiles_versions_and_paths():
    settings = Settings.from_env(
        {
            "SPARK_HOSTNAME": "Spark.Example.com ",
            "COMPOSE_PROFILES": "tunnel, media",
            "COSMOS_VERSION": "0.23.4",
            "CLOUDFLARED_VERSION": "",
            "COSMOS_SERVER_COUNTRY": "in",
            "SPARK_CLI_STATE_DIR": "/tmp/state",
        }
    )
    assert settings.hostname == "spark.example.com"
    assert settings.compose_profiles == ("tunnel", "media")
    assert settings.versions == {"cosmos": "0.23.4"}
    assert settings.server_country == "IN"
    assert str(settings.state_dir).replace("\\", "/") == "/tmp/state"
    assert not settings.quick_tunnel


def test_settings_require_helpers_explain_what_is_missing():
    with pytest.raises(SparkError, match="SPARK_HOSTNAME"):
        Settings().require_hostname()
    with pytest.raises(SparkError, match="CF_API_TOKEN, CF_ACCOUNT_ID, CF_ZONE_ID"):
        Settings().require_cloudflare()


def test_state_round_trip(tmp_path):
    state = State(tmp_path)
    state.set("token", "cosmos_abc")
    assert State(tmp_path).get("token") == "cosmos_abc"
    state.delete("token")
    assert State(tmp_path).get("token") is None


def test_demultiplex_handles_framed_and_plain_logs():
    framed = b"".join(
        struct.pack(">BxxxI", stream, len(payload)) + payload
        for stream, payload in ((1, b"hello "), (2, b"world\n"))
    )
    assert demultiplex(framed) == "hello world\n"
    assert demultiplex(b"plain tty output\n") == "plain tty output\n"


def test_find_container_ignores_one_off_run_containers():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/containers/json"
        return httpx.Response(
            200,
            json=[
                {"Id": "oneoff", "Labels": {"com.docker.compose.oneoff": "True"}},
                {"Id": "real", "Labels": {"com.docker.compose.oneoff": "False"}},
            ],
        )

    docker = DockerClient(httpx.Client(transport=httpx.MockTransport(handler), base_url="http://d"))
    assert docker.find_container("cosmos")["Id"] == "real"


def test_cli_without_command_prints_help(capsys):
    assert main([]) == 2
    assert "usage: spark" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("hostname", "url"),
    [
        ("spark.example.com", "https://spark.example.com"),
        ("lucky-cat.trycloudflare.com", "https://lucky-cat.trycloudflare.com"),
        ("spark.localhost", "http://spark.localhost"),
        ("localhost", "http://localhost"),
        ("notlocalhost", "https://notlocalhost"),
    ],
)
def test_public_url_is_http_only_for_laptop_only_names(hostname, url):
    assert public_url(hostname) == url
