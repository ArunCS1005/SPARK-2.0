import argparse
import json
import os
from pathlib import Path

import pytest

from spark_cli import filebrowser, seed
from spark_cli.cli import build_parser, main
from spark_cli.commands import init
from spark_cli.errors import SparkError
from spark_cli.settings import Settings

FOLDERS = ["users", "shared", "media/movies", "media/shows", "media/music", "backups"]
SEED = {
    "NewInstall": True,
    "HTTPConfig": {"HTTPSCertificateMode": "DISABLED", "HTTPPort": "80", "Hostname": "0.0.0.0"},
    "DockerConfig": {"SkipPruneNetwork": False, "SkipPruneImages": False},
    "ServerCountry": "",
    "AutoUpdate": True,
}
# The placeholder lines of config/filebrowser/config.yaml.
TEMPLATE = """\
http:
  externalUrl: "https://${SPARK_HOSTNAME}/files"
auth:
  adminUsername: "${SPARK_ADMIN_USERNAME}"
  methods:
    oidc:
      clientSecret: "${FB_OIDC_CLIENT_SECRET}"
      issuerUrl: "http://${SPARK_HOSTNAME}"
"""


@pytest.fixture
def settings(tmp_path: Path):
    config_dir = tmp_path / "config"
    (config_dir / "cosmos").mkdir(parents=True)
    (config_dir / "cosmos" / "seed.json").write_text(json.dumps(SEED), encoding="utf-8")
    (config_dir / "filebrowser").mkdir()
    (config_dir / "filebrowser" / "config.yaml").write_text(TEMPLATE, encoding="utf-8")

    def factory(**overrides) -> Settings:
        values = {
            "hostname": "spark.example.com",
            "server_country": "IN",
            "data_dir": tmp_path / "data",
            "volumes_dir": tmp_path / "volumes",
            "config_dir": config_dir,
        }
        return Settings(**{**values, **overrides})

    return factory


def run(settings) -> int:
    return init.run(argparse.Namespace(), settings)


def snapshot(root: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()
    }


def test_init_is_registered():
    assert "init" in build_parser().format_help()


def test_first_run_prepares_folders_and_both_configs(settings, capsys):
    s = settings()
    assert run(s) == 0

    for name in FOLDERS:
        assert (s.data_dir / name).is_dir()
    cosmos = json.loads(seed.config_path(s.volumes_dir).read_text(encoding="utf-8"))
    assert cosmos["HTTPConfig"]["Hostname"] == "spark.example.com"
    assert cosmos["ServerCountry"] == "IN"
    assert cosmos["AutoUpdate"] is False

    fb = filebrowser.config_path(s.volumes_dir).read_text(encoding="utf-8")
    assert '"https://spark.example.com/files"' in fb
    assert '"pending-setup"' in fb
    assert '"spark-pending"' in fb
    assert "${" not in fb

    out = capsys.readouterr().out.splitlines()
    assert out == [
        f"[ok]   Created data folders in {s.data_dir}: {', '.join(FOLDERS)}",
        "[ok]   Cosmos config created from the seed (new install)",
        "[ok]   FileBrowser placeholder config written (spark setup replaces it)",
    ]


def test_second_run_changes_nothing(settings, capsys, tmp_path):
    s = settings()
    run(s)
    (s.data_dir / "users" / "alice").mkdir()
    (s.data_dir / "shared" / "notes.txt").write_text("keep me")
    before = snapshot(tmp_path)
    capsys.readouterr()

    assert run(s) == 0

    assert snapshot(tmp_path) == before
    assert capsys.readouterr().out.splitlines() == [
        f"[ok]   Data folders present in {s.data_dir}",
        "[ok]   Cosmos config present; safety settings ok",
        "[ok]   FileBrowser config present (left unchanged)",
    ]


def test_only_missing_folders_are_created(settings):
    s = settings()
    (s.data_dir / "media" / "movies").mkdir(parents=True)
    (s.data_dir / "users").mkdir()
    created = init.create_data_folders(s.data_dir)
    assert created == ["shared", "media/shows", "media/music", "backups"]
    assert init.create_data_folders(s.data_dir) == []


@pytest.mark.skipif(not hasattr(os, "geteuid") or os.geteuid() != 0, reason="needs root")
def test_new_filebrowser_folders_belong_to_uid_1000(settings):
    s = settings()
    (s.data_dir / "shared").mkdir(parents=True)  # existing: owner left alone
    init.create_data_folders(s.data_dir)
    assert (s.data_dir / "users").stat().st_uid == 1000
    assert (s.data_dir / "shared").stat().st_uid == 0
    assert (s.data_dir / "backups").stat().st_uid == 0


def test_existing_filebrowser_config_is_never_overwritten(settings, capsys):
    s = settings()
    path = filebrowser.config_path(s.volumes_dir)
    path.parent.mkdir(parents=True)
    path.write_text("rendered by spark setup\n", encoding="utf-8")

    assert run(s) == 0
    assert path.read_text(encoding="utf-8") == "rendered by spark setup\n"
    assert "FileBrowser config present (left unchanged)" in capsys.readouterr().out


def test_quick_tunnel_mode_without_hostname(settings):
    s = settings(hostname="", compose_profiles=("quick-tunnel",))
    assert run(s) == 0

    cosmos = json.loads(seed.config_path(s.volumes_dir).read_text(encoding="utf-8"))
    assert cosmos["HTTPConfig"]["Hostname"] == "0.0.0.0"
    fb = filebrowser.config_path(s.volumes_dir).read_text(encoding="utf-8")
    assert '"https://localhost/files"' in fb


def test_unsafe_existing_cosmos_config_is_repaired_with_a_warning(settings, capsys):
    s = settings()
    run(s)
    path = seed.config_path(s.volumes_dir)
    config = json.loads(path.read_text(encoding="utf-8"))
    config["AutoUpdate"] = True
    config["DockerConfig"]["SkipPruneNetwork"] = False
    path.write_text(json.dumps(config), encoding="utf-8")
    capsys.readouterr()

    assert run(s) == 0

    assert seed.enforce_safety(json.loads(path.read_text(encoding="utf-8"))) == []
    assert (
        "[warn] Cosmos config had unsafe values; put back AutoUpdate, "
        "DockerConfig.SkipPruneNetwork" in capsys.readouterr().out
    )


def test_cli_fails_closed_without_a_seed(settings, monkeypatch, capsys):
    s = settings()
    (s.config_dir / "cosmos" / "seed.json").unlink()
    for name, value in {
        "SPARK_HOSTNAME": "spark.example.com",
        "SPARK_CLI_DATA_DIR": str(s.data_dir),
        "SPARK_CLI_VOLUMES_DIR": str(s.volumes_dir),
        "SPARK_CLI_CONFIG_DIR": str(s.config_dir),
    }.items():
        monkeypatch.setenv(name, value)

    assert main(["init"]) == 1

    assert "Cosmos seed config not found" in capsys.readouterr().err
    assert not seed.config_path(s.volumes_dir).exists()


def test_missing_filebrowser_template_is_an_error(settings):
    s = settings()
    (s.config_dir / "filebrowser" / "config.yaml").unlink()
    with pytest.raises(SparkError, match="FileBrowser config template"):
        run(s)
    assert not filebrowser.config_path(s.volumes_dir).exists()
