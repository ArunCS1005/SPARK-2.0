"""Cosmos's config file before its first start (docs/development.md, "Cosmos safety").

Cosmos holds the Docker socket and, unless its config file says otherwise, prunes every unused image
and network on the machine and replaces its own container on updates. There are no environment
variables for this, so `spark init` writes the file before Cosmos ever starts. Key names are the Go
field names of Cosmos's `Config` struct (utils/types.go has no json tags on them)."""

import copy
import json
import os
from pathlib import Path
from typing import Any

from spark_cli.errors import SparkError

CONFIG_FILENAME = "cosmos.config.json"
SEED_FILE = Path("cosmos") / "seed.json"  # relative to the repo config/ folder

# Dotted key -> the only value SPARK accepts.
SAFETY_KEYS: dict[str, bool] = {
    "AutoUpdate": False,
    "DockerConfig.SkipPruneNetwork": True,
    "DockerConfig.SkipPruneImages": True,
}

# Quick-tunnel mode has no hostname until `spark setup` reads it from the tunnel logs. This is
# Cosmos's own default (utils/utils.go DefaultConfig), and its hostname checks accept any Host
# header while the hostname is 0.0.0.0 (utils/middleware.go EnsureHostname,
# EnsureHostnameCosmosAPI, IsValidHostname).
ANY_HOSTNAME = "0.0.0.0"

_REGENERATE_HINT = (
    "Regenerate config/cosmos/seed.json from the pinned COSMOS_VERSION "
    "(docs/development.md, 'Cosmos safety'). Cosmos stays stopped until then."
)


def seed_config(seed: dict, *, hostname: str, country: str) -> dict:
    """The config Cosmos starts with on a new install: the seed plus SPARK's values. Every key
    SPARK sets must already exist in the seed, so a seed from a Cosmos version that renamed a
    safety key is refused instead of being silently ignored by Cosmos."""
    config = copy.deepcopy(seed)
    values: dict[str, Any] = {
        "NewInstall": True,
        **SAFETY_KEYS,
        "HTTPConfig.HTTPSCertificateMode": "DISABLED",
        "HTTPConfig.Hostname": _check_hostname(hostname) or ANY_HOSTNAME,
    }
    if country:
        values["ServerCountry"] = country
    for key, value in values.items():
        parent, name = _parent(config, key)
        if parent is None or name not in parent:
            raise SparkError(
                f"config/cosmos/seed.json has no '{key}'; it does not match this Cosmos version.",
                hint=_REGENERATE_HINT,
            )
        parent[name] = value
    return config


def enforce_safety(config: dict) -> list[str]:
    """Put back the three safety keys in place; returns the dotted names it changed."""
    changed = []
    for key, value in SAFETY_KEYS.items():
        parent, name = _parent(config, key, create=True)
        assert parent is not None
        # Exact bools only: anything else is either unsafe or a value Cosmos cannot load.
        if parent.get(name) is not value:
            parent[name] = value
            changed.append(key)
    return changed


def repair_safety_keys(volumes_dir: Path) -> list[str]:
    """Put back the safety keys in an existing config file (for a restore, which starts Cosmos
    without init). Returns the keys it changed; no file, no change."""
    path = config_path(volumes_dir)
    if not path.is_file():
        return []
    config = _read_json(path, what="Cosmos config", hint="")
    changed = enforce_safety(config)
    if changed:
        _write_json(path, config)
    return changed


def config_path(volumes_dir: Path) -> Path:
    return Path(volumes_dir) / "cosmos-config" / CONFIG_FILENAME


def load_seed(config_dir: Path) -> dict:
    return _read_json(
        Path(config_dir) / SEED_FILE, what="Cosmos seed config", hint=_REGENERATE_HINT
    )


def last_good_path(volumes_dir: Path) -> Path:
    """SPARK's copy of the last config that loaded, kept next to Cosmos's files in its volume."""
    return Path(volumes_dir) / "cosmos-config" / ".spark" / "cosmos.config.last-good.json"


def ensure_cosmos_config(
    volumes_dir: Path, config_dir: Path, *, hostname: str, country: str
) -> tuple[str, list[str]]:
    """write_cosmos_config, plus the safety keys a repair or restore changed (for the warning).

    Cosmos rewrites its config in place, so a crash can leave it empty or cut off, and an
    accidentally deleted file would make Cosmos start a brand-new install whose setup deletes every
    account. Each run therefore keeps a copy of the last config that loaded and puts it back when
    the file is missing or unreadable. Without a copy, only a folder that never held a Cosmos
    install is seeded; anything else fails closed so Cosmos stays stopped."""
    path = config_path(volumes_dir)
    last_good = last_good_path(volumes_dir)

    if path.exists():
        try:
            config = _read_json(path, what="Cosmos config", hint="")
        except SparkError as exc:
            if not last_good.is_file():
                raise SparkError(
                    exc.message,
                    hint="SPARK has no earlier copy to put back, and Cosmos stays stopped. Restore "
                    "a backup (spark restore <archive>) or repair the file.",
                ) from exc
            path.replace(path.with_name(f".{path.name}.broken"))
            return _restore(path, last_good)
        changed = enforce_safety(config)
        if changed:
            _write_json(path, config)
        _write_json(last_good, config)
        return ("repaired" if changed else "ok"), changed

    if last_good.is_file():
        return _restore(path, last_good)
    if _holds_an_install(path.parent):
        raise SparkError(
            f"Cosmos's config file {path} is missing but the rest of its install is still there.",
            hint="Seeding a new config would reopen first-run setup, which deletes all accounts. "
            "Restore a backup (spark restore <archive>) instead.",
        )
    config = seed_config(load_seed(config_dir), hostname=hostname, country=country)
    _write_json(path, config)
    _write_json(last_good, config)
    return "created", []


def _holds_an_install(folder: Path) -> bool:
    """Anything Cosmos wrote (its database, logs) besides SPARK's own .spark folder."""
    return folder.is_dir() and any(p.name != ".spark" for p in folder.iterdir())


def _restore(path: Path, last_good: Path) -> tuple[str, list[str]]:
    config = _read_json(last_good, what="SPARK's copy of the Cosmos config", hint="")
    changed = enforce_safety(config)
    _write_json(path, config)
    return "restored", changed


def write_cosmos_config(volumes_dir: Path, config_dir: Path, *, hostname: str, country: str) -> str:
    """Create Cosmos's config from the seed, repair the safety keys of an existing one, or put
    back the last good copy. Returns "created", "repaired", "restored" or "ok"."""
    return ensure_cosmos_config(volumes_dir, config_dir, hostname=hostname, country=country)[0]


def _check_hostname(hostname: str) -> str:
    # Cosmos rejects commas, slashes and spaces in its hostname. A port is worse: Cosmos then starts
    # a helper container that recreates its own container to publish that port
    # (docker/checkPorts.go).
    if any(c in hostname for c in ":/,") or any(c.isspace() for c in hostname):
        raise SparkError(
            f"SPARK_HOSTNAME '{hostname}' is not a bare hostname.",
            hint="Use the name only, without scheme, port or path (spark.example.com).",
        )
    return hostname


def _parent(config: dict, key: str, create: bool = False) -> tuple[dict | None, str]:
    *path, name = key.split(".")
    node = config
    for part in path:
        child = node.get(part)
        if not isinstance(child, dict):
            if not create:
                return None, name
            child = node[part] = {}
        node = child
    return node, name


def _read_json(path: Path, *, what: str, hint: str) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SparkError(f"{what} not found: {path}", hint=hint) from None
    except (OSError, ValueError) as exc:
        raise SparkError(f"{what} {path} cannot be read as JSON: {exc}", hint=hint) from exc
    if not isinstance(data, dict):
        raise SparkError(f"{what} {path} is not a JSON object.", hint=hint)
    return data


def _write_json(path: Path, data: dict) -> None:
    """Atomic write, mode 0600, formatted like Cosmos writes it (two-space indent)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
