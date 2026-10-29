import io
import json
import os
import stat
import tarfile

import pytest

from spark_cli import seed
from spark_cli.cli import build_parser
from spark_cli.commands import backup
from spark_cli.docker_api import DockerClient
from spark_cli.errors import SparkError

ROOT = hasattr(os, "geteuid") and os.geteuid() == 0


class FakeDocker:
    def __init__(self, running=("cosmos", "filebrowser")):
        self.containers = {
            s: {"Id": f"{s}-id", "State": "running" if s in running else "exited"}
            for s in ("cosmos", "filebrowser")
        }
        self.calls: list[str] = []

    def ping(self):
        return True

    def find_container(self, service):
        return self.containers.get(service)

    def stop(self, container_id, timeout=20):
        self.calls.append(f"stop {container_id}")

    def start(self, container_id):
        self.calls.append(f"start {container_id}")


@pytest.fixture
def docker(monkeypatch):
    fake = FakeDocker()
    monkeypatch.setattr(DockerClient, "from_socket", staticmethod(lambda *a, **k: fake))
    return fake


@pytest.fixture
def install(make_settings):
    settings = make_settings(versions={"cosmos": "0.23.4"})
    cosmos = settings.volumes_dir / "cosmos-config"
    (cosmos / ".spark").mkdir(parents=True)
    config = {
        "AutoUpdate": False,
        "DockerConfig": {"SkipPruneNetwork": True, "SkipPruneImages": True},
    }
    (cosmos / "cosmos.config.json").write_text(json.dumps(config))
    (cosmos / "auth.db").write_bytes(b"accounts")
    (cosmos / ".spark" / "cosmos.config.last-good.json").write_text(json.dumps(config))
    fb = settings.volumes_dir / "filebrowser-data"
    fb.mkdir()
    (fb / "config.yaml").write_text("secret: s3cret\n")
    os.chmod(fb / "config.yaml", 0o600)
    (fb / "database.sqlite").write_bytes(b"files db")
    if ROOT:
        for path in (fb, fb / "config.yaml", fb / "database.sqlite"):
            os.chown(path, 1000, 1000)
    settings.state_dir.mkdir()
    (settings.state_dir / "state.json").write_text('{"cosmos.admin_token": "cosmos_tok"}')
    (settings.state_dir / "state.tmp").write_text("half-written")
    return settings


def spark(settings, *argv) -> int:
    args = build_parser().parse_args(list(argv))
    return args.handler(args, settings)


def only_archive(settings, pattern="spark-2*.tar.gz"):
    [path] = (settings.data_dir / "backups").glob(pattern)
    return path


def test_backup_archives_volumes_and_state_with_services_stopped(install, docker, capsys):
    assert spark(install, "backup") == 0
    path = only_archive(install)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    with tarfile.open(path) as tar:
        names = tar.getnames()
        manifest = json.load(tar.extractfile("manifest.json"))
        if ROOT:
            assert tar.getmember("filebrowser-data/config.yaml").uid == 1000
    assert "cosmos-config/auth.db" in names
    assert "cosmos-config/.spark/cosmos.config.last-good.json" in names
    assert "filebrowser-data/database.sqlite" in names
    assert "state/state.json" in names
    assert "state/state.tmp" not in names
    assert manifest["format"] == 1
    assert manifest["versions"] == {"cosmos": "0.23.4"}
    assert docker.calls == [
        "stop filebrowser-id",
        "stop cosmos-id",
        "start cosmos-id",
        "start filebrowser-id",
    ]
    assert "keep it private" in capsys.readouterr().out


def test_backup_starts_services_again_when_it_fails(install, docker, monkeypatch):
    def broken(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(backup.tarfile, "open", broken)
    with pytest.raises(OSError):
        spark(install, "backup")
    assert docker.calls[-2:] == ["start cosmos-id", "start filebrowser-id"]
    assert list((install.data_dir / "backups").iterdir()) == []


def test_backup_leaves_stopped_services_stopped(install, monkeypatch):
    fake = FakeDocker(running=("cosmos",))
    monkeypatch.setattr(DockerClient, "from_socket", staticmethod(lambda *a, **k: fake))
    spark(install, "backup")
    assert fake.calls == ["stop cosmos-id", "start cosmos-id"]


def test_restore_round_trip(install, docker, capsys):
    spark(install, "backup")
    archive = only_archive(install)
    cosmos = install.volumes_dir / "cosmos-config"
    (cosmos / "auth.db").write_bytes(b"accounts changed later")
    (cosmos / "new-file").write_text("not in the backup")
    (install.state_dir / "state.json").write_text("{}")
    docker.calls.clear()

    assert spark(install, "restore", str(archive), "--yes") == 0

    assert (cosmos / "auth.db").read_bytes() == b"accounts"
    assert not (cosmos / "new-file").exists()
    assert "cosmos_tok" in (install.state_dir / "state.json").read_text()
    config = install.volumes_dir / "filebrowser-data" / "config.yaml"
    assert stat.S_IMODE(config.stat().st_mode) == 0o600
    if ROOT:
        assert config.stat().st_uid == 1000
        assert config.parent.stat().st_uid == 1000
    assert docker.calls[0:2] == ["stop filebrowser-id", "stop cosmos-id"]
    assert docker.calls[-2:] == ["start cosmos-id", "start filebrowser-id"]
    # The settings it replaced were saved first.
    before = only_archive(install, "spark-before-restore-*.tar.gz")
    with tarfile.open(before) as tar:
        assert tar.extractfile("cosmos-config/auth.db").read() == b"accounts changed later"
    assert not list((install.data_dir / "backups").glob(".restore-*"))


def test_restore_puts_back_unsafe_cosmos_settings(install, docker, capsys):
    path = seed.config_path(install.volumes_dir)
    path.write_text(json.dumps({"AutoUpdate": True, "DockerConfig": {"SkipPruneImages": False}}))
    spark(install, "backup")
    spark(install, "restore", str(only_archive(install)), "--yes")
    config = json.loads(path.read_text())
    assert config["AutoUpdate"] is False
    assert config["DockerConfig"] == {"SkipPruneImages": True, "SkipPruneNetwork": True}
    assert "unsafe Cosmos settings" in capsys.readouterr().out


def hostile_archive(tmp_path, *members, manifest=True):
    path = tmp_path / "hostile.tar.gz"
    with tarfile.open(path, "w:gz") as tar:
        if manifest:
            add_bytes(tar, "manifest.json", json.dumps({"format": 1}).encode())
        for member in members:
            if isinstance(member, tarfile.TarInfo):
                tar.addfile(member)
            else:
                add_bytes(tar, member, b"x")
    return path


def add_bytes(tar, name, data):
    info = tarfile.TarInfo(name)
    info.size = len(data)
    tar.addfile(info, io.BytesIO(data))


def link(name, target, kind):
    info = tarfile.TarInfo(name)
    info.type = kind
    info.linkname = target
    return info


@pytest.mark.parametrize(
    "member",
    [
        "state/../../escape.txt",
        "/etc/cron.d/evil",
        "somewhere-else/file",
        link("state/state.json", "/etc/shadow", tarfile.SYMTYPE),
        link("state/hard", "/etc/passwd", tarfile.LNKTYPE),
    ],
)
def test_restore_refuses_hostile_archives_before_stopping_anything(
    install, docker, tmp_path, member
):
    archive = hostile_archive(tmp_path, member)
    with pytest.raises(SparkError, match="never has. Nothing was restored"):
        spark(install, "restore", str(archive), "--yes")
    assert docker.calls == []
    assert not (tmp_path / "escape.txt").exists()


def test_restore_refuses_archives_without_manifest(install, docker, tmp_path):
    archive = hostile_archive(tmp_path, "state/state.json", manifest=False)
    with pytest.raises(SparkError, match="not a SPARK backup"):
        spark(install, "restore", str(archive), "--yes")
    assert docker.calls == []


def test_restore_refuses_files_that_are_not_archives(install, docker, tmp_path):
    archive = tmp_path / "notes.tar.gz"
    archive.write_text("hello")
    with pytest.raises(SparkError, match="not a readable SPARK backup"):
        spark(install, "restore", str(archive), "--yes")


def test_restore_needs_confirmation_without_terminal(install, docker, monkeypatch):
    spark(install, "backup")
    docker.calls.clear()
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    with pytest.raises(SparkError, match="without confirmation"):
        spark(install, "restore", str(only_archive(install)))
    assert docker.calls == []


def test_restore_failing_half_way_says_how_to_undo(install, docker, monkeypatch):
    spark(install, "backup")

    def broken(source, target):
        raise OSError("disk full")

    monkeypatch.setattr(backup, "copy_with_owner", broken)
    with pytest.raises(SparkError, match="Restore stopped half-way") as exc:
        spark(install, "restore", str(only_archive(install)), "--yes")
    assert "spark-before-restore-" in exc.value.hint
    assert docker.calls[-2:] == ["start cosmos-id", "start filebrowser-id"]


@pytest.mark.skipif(not ROOT, reason="needs root to change owners")
def test_archive_belongs_to_the_owner_of_spark_data(install, docker):
    install.data_dir.mkdir(exist_ok=True)
    os.chown(install.data_dir, 1234, 1234)
    spark(install, "backup")
    archive = only_archive(install)
    assert (archive.stat().st_uid, stat.S_IMODE(archive.stat().st_mode)) == (1234, 0o600)
