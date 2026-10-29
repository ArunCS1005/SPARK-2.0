import copy
import json
import stat
from pathlib import Path

import pytest

from spark_cli import seed
from spark_cli.errors import SparkError

# tests -> spark-cli -> tools -> repository (the test container mounts the repository at /repo).
REPO_SEED = Path(__file__).resolve().parent.parent.parent.parent / "config/cosmos/seed.json"

# Shaped like the file Cosmos writes from DefaultConfig (utils/utils.go): Go field names as keys,
# in struct order, unsafe defaults included.
SEED = {
    "LoggingLevel": "INFO",
    "Database": {"PostgresHost": "", "NodeName": ""},
    "DisableUserManagement": False,
    "NewInstall": True,
    "HTTPConfig": {
        "AuthPrivateKey": "",
        "GenerateMissingAuthCert": True,
        "HTTPSCertificateMode": "DISABLED",
        "HTTPPort": "80",
        "HTTPSPort": "443",
        "ProxyConfig": {"Routes": []},
        "Hostname": "0.0.0.0",
        "AllowHTTPLocalIPAccess": False,
        "PublishMDNS": True,
        "TrustedProxies": None,
    },
    "DockerConfig": {"SkipPruneNetwork": False, "SkipPruneImages": False, "DefaultDataPath": ""},
    "BlockedCountries": [],
    "ServerCountry": "",
    "RequireMFA": False,
    "AutoUpdate": True,
    "BetaUpdates": False,
    "OpenIDClients": None,
    "MonitoringAlerts": {"Disk Full Notification": {"Enabled": True, "Condition": {"Value": 95}}},
}


@pytest.fixture
def dirs(tmp_path: Path) -> tuple[Path, Path]:
    volumes, config = tmp_path / "volumes", tmp_path / "config"
    (config / "cosmos").mkdir(parents=True)
    (config / "cosmos" / "seed.json").write_text(json.dumps(SEED, indent=2), encoding="utf-8")
    return volumes, config


def read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_seed_config_applies_spark_values_and_keeps_the_rest():
    original = copy.deepcopy(SEED)
    config = seed.seed_config(SEED, hostname="spark.example.com", country="IN")

    assert original == SEED  # input untouched
    assert config["NewInstall"] is True
    assert config["AutoUpdate"] is False
    assert config["DockerConfig"]["SkipPruneNetwork"] is True
    assert config["DockerConfig"]["SkipPruneImages"] is True
    assert config["HTTPConfig"]["HTTPSCertificateMode"] == "DISABLED"
    assert config["HTTPConfig"]["Hostname"] == "spark.example.com"
    assert config["ServerCountry"] == "IN"
    assert list(config) == list(SEED)
    assert list(config["HTTPConfig"]) == list(SEED["HTTPConfig"])
    assert config["MonitoringAlerts"] == SEED["MonitoringAlerts"]


def test_seed_config_without_hostname_or_country_uses_cosmos_defaults():
    config = seed.seed_config(SEED, hostname="", country="")
    assert config["HTTPConfig"]["Hostname"] == "0.0.0.0"
    assert config["ServerCountry"] == ""


@pytest.mark.parametrize(
    "hostname", ["spark.example.com:8080", "https://spark.example.com", "a.com,b.com", "a b"]
)
def test_seed_config_rejects_anything_but_a_bare_hostname(hostname):
    with pytest.raises(SparkError, match="bare hostname"):
        seed.seed_config(SEED, hostname=hostname, country="")


@pytest.mark.parametrize("missing", ["DockerConfig.SkipPruneNetwork", "AutoUpdate", "HTTPConfig"])
def test_seed_config_refuses_a_seed_without_a_key_spark_sets(missing):
    broken = copy.deepcopy(SEED)
    *parents, name = missing.split(".")
    node = broken
    for part in parents:
        node = node[part]
    del node[name]
    with pytest.raises(SparkError, match="does not match this Cosmos version"):
        seed.seed_config(broken, hostname="spark.example.com", country="")


def test_enforce_safety_reports_only_what_it_changed():
    config = {"AutoUpdate": True, "DockerConfig": {"SkipPruneNetwork": True, "SkipPruneImages": 0}}
    assert seed.enforce_safety(config) == ["AutoUpdate", "DockerConfig.SkipPruneImages"]
    assert config == {
        "AutoUpdate": False,
        "DockerConfig": {"SkipPruneNetwork": True, "SkipPruneImages": True},
    }
    assert seed.enforce_safety(config) == []


def test_enforce_safety_adds_missing_keys():
    config: dict = {"DockerConfig": None}
    assert seed.enforce_safety(config) == list(seed.SAFETY_KEYS)
    assert config["DockerConfig"] == {"SkipPruneNetwork": True, "SkipPruneImages": True}


def test_write_cosmos_config_creates_the_file_from_the_seed(dirs):
    volumes, config_dir = dirs
    result = seed.write_cosmos_config(
        volumes, config_dir, hostname="spark.example.com", country="IN"
    )

    path = volumes / "cosmos-config" / "cosmos.config.json"
    assert result == "created"
    assert read(path) == seed.seed_config(SEED, hostname="spark.example.com", country="IN")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert path.read_text(encoding="utf-8").startswith('{\n  "LoggingLevel"')
    # SPARK's last-good copy, and no temp file left behind.
    assert sorted(p.name for p in path.parent.iterdir()) == [".spark", "cosmos.config.json"]
    assert read(seed.last_good_path(volumes)) == read(path)


def test_write_cosmos_config_leaves_a_safe_existing_file_alone(dirs):
    volumes, config_dir = dirs
    seed.write_cosmos_config(volumes, config_dir, hostname="spark.example.com", country="IN")
    path = seed.config_path(volumes)
    before = path.read_bytes()

    # Different settings must not matter once the file exists: Cosmos owns it now.
    assert (
        seed.write_cosmos_config(volumes, config_dir, hostname="other.example", country="") == "ok"
    )
    assert path.read_bytes() == before


def test_write_cosmos_config_repairs_safety_keys_and_keeps_everything_else(dirs):
    volumes, config_dir = dirs
    path = seed.config_path(volumes)
    path.parent.mkdir(parents=True)
    # A file as Cosmos leaves it after setup: secrets, routes and clients, keys in its own order.
    existing = {
        "NewInstall": False,
        "AutoUpdate": True,
        "HTTPConfig": {
            "Hostname": "spark.example.com",
            "AuthPrivateKey": "secret",
            "HTTPPort": "80",
        },
        "OpenIDClients": [{"id": "filebrowser", "secret": "$2a$10$hash"}],
        "DockerConfig": {
            "DefaultDataPath": "/x",
            "SkipPruneImages": False,
            "SkipPruneNetwork": True,
        },
        "ServerCountry": "IN",
        "Ünïcode": "naïve",
        "Ratio": 0.5,
    }
    path.write_text(json.dumps(existing, indent=2), encoding="utf-8")

    result, changed = seed.ensure_cosmos_config(
        volumes, config_dir, hostname="spark.example.com", country="IN"
    )

    assert (result, changed) == ("repaired", ["AutoUpdate", "DockerConfig.SkipPruneImages"])
    repaired = read(path)
    expected = copy.deepcopy(existing)
    expected["AutoUpdate"] = False
    expected["DockerConfig"]["SkipPruneImages"] = True
    assert repaired == expected
    assert list(repaired) == list(existing)
    assert list(repaired["DockerConfig"]) == list(existing["DockerConfig"])
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert seed.write_cosmos_config(volumes, config_dir, hostname="", country="") == "ok"


@pytest.mark.parametrize("content", ["{ not json", "", "[1, 2]"])
def test_write_cosmos_config_never_overwrites_an_unreadable_file(dirs, content):
    volumes, config_dir = dirs
    path = seed.config_path(volumes)
    path.parent.mkdir(parents=True)
    path.write_text(content, encoding="utf-8")

    with pytest.raises(SparkError, match="Cosmos config") as excinfo:
        seed.write_cosmos_config(volumes, config_dir, hostname="spark.example.com", country="")
    assert "no earlier copy" in excinfo.value.hint
    assert path.read_text(encoding="utf-8") == content


@pytest.mark.parametrize("content", [None, "{ not json", '"a string"'])
def test_write_cosmos_config_fails_closed_without_a_usable_seed(tmp_path, content):
    config_dir = tmp_path / "config"
    (config_dir / "cosmos").mkdir(parents=True)
    if content is not None:
        (config_dir / "cosmos" / "seed.json").write_text(content, encoding="utf-8")

    with pytest.raises(SparkError, match="Cosmos seed config"):
        seed.write_cosmos_config(tmp_path / "volumes", config_dir, hostname="h.example", country="")
    assert not seed.config_path(tmp_path / "volumes").exists()


@pytest.mark.skipif(
    not REPO_SEED.exists(), reason="config/cosmos/seed.json not generated or not mounted"
)
def test_repo_seed_carries_every_key_spark_sets():
    config = seed.seed_config(
        json.loads(REPO_SEED.read_text(encoding="utf-8")),
        hostname="spark.example.com",
        country="IN",
    )
    assert seed.enforce_safety(config) == []


def test_missing_config_is_restored_from_the_last_good_copy(dirs):
    volumes, config_dir = dirs
    seed.write_cosmos_config(volumes, config_dir, hostname="spark.example.com", country="IN")
    path = seed.config_path(volumes)
    good = read(path)
    good["NewInstall"] = False  # as after setup
    path.write_text(json.dumps(good), encoding="utf-8")
    assert seed.write_cosmos_config(volumes, config_dir, hostname="", country="") == "ok"

    path.unlink()
    assert seed.write_cosmos_config(volumes, config_dir, hostname="", country="") == "restored"
    assert read(path) == good


def test_corrupt_config_is_set_aside_and_restored(dirs):
    volumes, config_dir = dirs
    seed.write_cosmos_config(volumes, config_dir, hostname="spark.example.com", country="IN")
    path = seed.config_path(volumes)
    good = read(path)
    path.write_text('{"NewInstall": fal', encoding="utf-8")  # cut off mid-write

    result, changed = seed.ensure_cosmos_config(volumes, config_dir, hostname="", country="")

    assert (result, changed) == ("restored", [])
    assert read(path) == good
    assert path.with_name(".cosmos.config.json.broken").read_text() == '{"NewInstall": fal'


def test_restored_copy_gets_the_safety_keys_back(dirs):
    volumes, config_dir = dirs
    last_good = seed.last_good_path(volumes)
    last_good.parent.mkdir(parents=True)
    last_good.write_text(json.dumps({"AutoUpdate": True, "DockerConfig": {}}), encoding="utf-8")

    result, changed = seed.ensure_cosmos_config(volumes, config_dir, hostname="", country="")

    assert result == "restored"
    assert set(changed) == set(seed.SAFETY_KEYS)
    assert seed.enforce_safety(read(seed.config_path(volumes))) == []


def test_missing_config_of_an_existing_install_fails_closed(dirs):
    volumes, config_dir = dirs
    folder = seed.config_path(volumes).parent
    folder.mkdir(parents=True)
    (folder / "auth.db").write_bytes(b"accounts")

    with pytest.raises(SparkError, match="missing") as excinfo:
        seed.write_cosmos_config(volumes, config_dir, hostname="spark.example.com", country="")
    assert "deletes all accounts" in excinfo.value.hint
    assert not seed.config_path(volumes).exists()
