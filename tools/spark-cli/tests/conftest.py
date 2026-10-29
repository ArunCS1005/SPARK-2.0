from pathlib import Path

import pytest

from spark_cli.settings import Settings


@pytest.fixture
def make_settings(tmp_path: Path):
    """Settings wired to temporary directories. Override any field with keyword arguments."""

    def factory(**overrides) -> Settings:
        values = {
            "hostname": "spark.example.com",
            "server_country": "IN",
            "state_dir": tmp_path / "state",
            "data_dir": tmp_path / "data",
            "volumes_dir": tmp_path / "volumes",
            "config_dir": Path(__file__).resolve().parents[3] / "config",
        }
        values.update(overrides)
        return Settings(**values)

    return factory
