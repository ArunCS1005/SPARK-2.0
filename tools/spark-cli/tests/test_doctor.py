import hashlib
import json
import os

import httpx
import pytest

from spark_cli import filebrowser, seed
from spark_cli.commands.doctor import Doctor
from spark_cli.cosmos import CosmosClient
from spark_cli.errors import SparkError
from spark_cli.state import State

HOST = "spark.example.com"
ROOT = os.geteuid() == 0 if hasattr(os, "geteuid") else False


class FakeDocker:
    def __init__(self):
        self.up = True
        self.containers = {
            "cosmos": container("azukaar/cosmos-server:0.23.4"),
            "filebrowser": container("gtstef/filebrowser:2.0.2-stable"),
            "tunnel": container("cloudflare/cloudflared:2026.10.0", healthy=None),
        }
        self.log_text = ""

    def ping(self):
        return self.up

    def find_container(self, service):
        if not self.up:
            raise SparkError("Cannot reach the Docker engine.")
        return self.containers.get(service)

    def logs(self, container_id, tail=500):
        return self.log_text


def container(image, healthy=True, state="running"):
    status = "Up 2 hours" + {True: " (healthy)", False: " (unhealthy)", None: ""}[healthy]
    return {"Id": image, "Image": image, "State": state, "Status": status}


@pytest.fixture
def install(make_settings):
    """A healthy install on disk: what `spark init` + `spark setup` leave behind."""
    settings = make_settings(
        compose_profiles=("tunnel",),
        versions={"cosmos": "0.23.4", "filebrowser": "2.0.2-stable", "cloudflared": "2026.10.0"},
    )
    config = seed.seed_config(seed.load_seed(settings.config_dir), hostname=HOST, country="IN")
    config["NewInstall"] = False
    config["HTTPConfig"]["ProxyConfig"]["Routes"] = [
        {"Name": "SPARK Files", "PathPrefix": "/files", "Target": "http://filebrowser:80"}
    ]
    config["OpenIDClients"] = [
        {"id": "filebrowser", "redirect": filebrowser.redirect_uri(HOST), "secret": "$2a$hash"}
    ]
    write_cosmos_config(settings, config)
    seed.last_good_path(settings.volumes_dir).parent.mkdir(parents=True, exist_ok=True)
    seed.last_good_path(settings.volumes_dir).write_text(json.dumps(config))

    template = (settings.config_dir / "filebrowser" / "config.yaml").read_text(encoding="utf-8")
    text = filebrowser.render_config(
        template,
        hostname=HOST,
        client_secret="s3cret",
        admin_username="arun",
        automation_secret="auto-s3cret",
    )
    filebrowser.write_config(settings.volumes_dir, text)
    for name in ("users", "shared", "backups"):
        (settings.data_dir / name).mkdir(parents=True)
        if ROOT:
            os.chown(settings.data_dir / name, 1000, 1000)
        else:
            os.chmod(settings.data_dir / name, 0o777)

    state = State(settings.state_dir)
    state.set("cosmos.admin_token", "cosmos_tok")
    state.set("setup.hostname", HOST)
    return settings


def write_cosmos_config(settings, config):
    path = seed.config_path(settings.volumes_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config))


def cosmos_config(settings):
    return json.loads(seed.config_path(settings.volumes_dir).read_text())


def run_doctor(settings, docker=None, status=200):
    def answer(request):
        assert request.headers["host"] == State(settings.state_dir).get("setup.hostname")
        if status != 200:
            return httpx.Response(status, json={"status": "error", "message": "nope"})
        return httpx.Response(200, json={"status": "OK", "data": {}})

    def factory(settings, token="", hostname=None):
        http = httpx.Client(transport=httpx.MockTransport(answer), base_url="http://cosmos:80")
        return CosmosClient(http, hostname, token)

    doctor = Doctor(
        settings, docker or FakeDocker(), State(settings.state_dir), cosmos_factory=factory
    )
    return doctor.run()


def failures(capsys):
    return [line for line in capsys.readouterr().out.splitlines() if line.startswith("[fail]")]


def tree_digest(*folders):
    digest = hashlib.sha256()
    for folder in folders:
        for path in sorted(p for p in folder.rglob("*")):
            stat = path.stat()
            digest.update(f"{path}{stat.st_mode}{stat.st_uid}{stat.st_mtime_ns}".encode())
            if path.is_file():
                digest.update(path.read_bytes())
    return digest.hexdigest()


@pytest.mark.skipif(not ROOT, reason="the healthy fixture needs root to own folders as uid 1000")
def test_healthy_install_passes_and_doctor_changes_nothing(install, capsys):
    before = tree_digest(install.volumes_dir, install.data_dir, install.state_dir)
    assert run_doctor(install) == 0
    out = capsys.readouterr().out
    assert "[fail]" not in out
    assert "[warn]" not in out
    assert "All checks passed." in out
    assert tree_digest(install.volumes_dir, install.data_dir, install.state_dir) == before


@pytest.mark.parametrize(
    "key", ["AutoUpdate", "DockerConfig.SkipPruneNetwork", "DockerConfig.SkipPruneImages"]
)
def test_unsafe_cosmos_settings_fail(install, capsys, key):
    config = cosmos_config(install)
    parent, name = seed._parent(config, key)
    parent[name] = not parent[name]
    write_cosmos_config(install, config)

    assert run_doctor(install) == 1
    [line] = [f for f in failures(capsys) if "safety" in f]
    assert key in line


def test_missing_cosmos_config_fails(install, capsys):
    seed.config_path(install.volumes_dir).unlink()
    assert run_doctor(install) == 1
    assert any("Cosmos config missing" in f for f in failures(capsys))


def test_cosmos_waiting_for_first_run_setup_fails(install, capsys):
    config = cosmos_config(install)
    config["NewInstall"] = True
    write_cosmos_config(install, config)
    assert run_doctor(install) == 1
    assert any("take it over" in f for f in failures(capsys))


@pytest.mark.parametrize(
    ("service", "change", "message"),
    [
        ("cosmos", {"Status": "Up 1 hour (unhealthy)"}, "cosmos: running but unhealthy"),
        ("filebrowser", {"State": "restarting", "Status": "Restarting (1)"}, "filebrowser:"),
        ("tunnel", None, "tunnel: no container"),
    ],
)
def test_broken_containers_fail(install, capsys, service, change, message):
    docker = FakeDocker()
    if change is None:
        del docker.containers[service]
    else:
        docker.containers[service].update(change)
    assert run_doctor(install, docker) == 1
    assert any(message in f for f in failures(capsys))


def test_unreachable_docker_fails_but_the_other_checks_still_run(install, capsys):
    docker = FakeDocker()
    docker.up = False
    assert run_doctor(install, docker) == 1
    out = capsys.readouterr().out
    assert "[fail] Cannot reach the Docker engine." in out
    assert "safety settings" in out


def test_version_drift_is_a_warning(install, capsys):
    docker = FakeDocker()
    docker.containers["cosmos"]["Image"] = "azukaar/cosmos-server:0.24.0"
    run_doctor(install, docker)
    assert "[warn] cosmos runs azukaar/cosmos-server:0.24.0, but .env pins 0.23.4" in (
        capsys.readouterr().out
    )


def test_rejected_token_fails(install, capsys):
    assert run_doctor(install, status=401) == 1
    assert any("rejects SPARK's admin token" in f for f in failures(capsys))


def test_hostname_changed_without_setup_fails(install, capsys):
    moved = type(install)(**{**install.__dict__, "hostname": "new.example.org"})
    assert run_doctor(moved) == 1
    assert any("Cosmos serves spark.example.com" in f for f in failures(capsys))


def test_laptop_only_hostname_on_another_port_fails(install, capsys):
    State(install.state_dir).set("setup.hostname", "spark.localhost")
    config = cosmos_config(install)
    config["HTTPConfig"]["Hostname"] = "spark.localhost"
    write_cosmos_config(install, config)
    local = type(install)(**{**install.__dict__, "hostname": "spark.localhost"})
    run_doctor(local)
    assert any("needs SPARK_HTTP_PORT=80" in f for f in failures(capsys))


def test_quick_tunnel_address_change_is_noticed(install, capsys):
    quick = type(install)(**{**install.__dict__, "hostname": "", "compose_profiles": ()})
    quick = type(install)(**{**quick.__dict__, "compose_profiles": ("quick-tunnel",)})
    docker = FakeDocker()
    docker.containers["quick-tunnel"] = container("cloudflare/cloudflared:2026.10.0", None)
    docker.log_text = "https://other-name.trycloudflare.com"
    assert run_doctor(quick, docker) == 1
    assert any("hostname is now other-name.trycloudflare.com" in f for f in failures(capsys))


def test_missing_files_route_and_client_fail(install, capsys):
    config = cosmos_config(install)
    config["HTTPConfig"]["ProxyConfig"]["Routes"] = []
    config["OpenIDClients"][0]["redirect"] = "https://old.example.com/files/api/auth/oidc/callback"
    write_cosmos_config(install, config)
    assert run_doctor(install) == 1
    found = failures(capsys)
    assert any("no /files route" in f for f in found)
    assert any("sign-in client in Cosmos is missing or outdated" in f for f in found)


def test_placeholder_filebrowser_config_fails(install, capsys):
    path = filebrowser.config_path(install.volumes_dir)
    path.write_text(path.read_text().replace('"s3cret"', '"pending-setup"'))
    assert run_doctor(install) == 1
    assert any("placeholder config" in f for f in failures(capsys))


@pytest.mark.skipif(not ROOT, reason="needs root to change owners")
def test_data_folder_filebrowser_cannot_write_fails(install, capsys):
    os.chown(install.data_dir / "users", 0, 0)
    os.chmod(install.data_dir / "users", 0o755)
    assert run_doctor(install) == 1
    assert any("cannot write to SPARK_DATA/users" in f for f in failures(capsys))


def test_local_only_install_does_not_need_filebrowser(install, capsys):
    local = type(install)(**{**install.__dict__, "hostname": "", "compose_profiles": ()})
    docker = FakeDocker()
    del docker.containers["filebrowser"]
    run_doctor(local, docker)
    out = capsys.readouterr().out
    assert "filebrowser" not in out.split("Cosmos safety")[0]
