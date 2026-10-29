import os
from pathlib import Path

import pytest

from spark_cli import filebrowser
from spark_cli.errors import SparkError

REPO_TEMPLATE = Path(__file__).resolve().parents[3] / "config" / "filebrowser" / "config.yaml"


def render(template: str, **overrides) -> str:
    values = {"hostname": "spark.example.com", "client_secret": "s3cret", "admin_username": "arun"}
    return filebrowser.render_config(template, **{**values, **overrides})


def test_repo_template_renders_completely():
    text = render(REPO_TEMPLATE.read_text(encoding="utf-8"))
    assert "${" not in text
    assert 'issuerUrl: "http://spark.example.com"' in text
    assert 'clientSecret: "s3cret"' in text
    assert 'adminUsername: "arun"' in text


@pytest.mark.parametrize("value", ['a"b', "a\\b", "a$b", "a\nb", ""])
def test_unsafe_values_are_refused(value):
    with pytest.raises(SparkError, match="FB_OIDC_CLIENT_SECRET"):
        render('secret: "${FB_OIDC_CLIENT_SECRET}"', client_secret=value)


def test_unknown_placeholder_is_refused():
    with pytest.raises(SparkError, match="HOME"):
        render("path: ${HOME}")


def test_redirect_uri():
    assert (
        filebrowser.redirect_uri("spark.example.com")
        == "https://spark.example.com/files/api/auth/oidc/callback"
    )


def test_write_config_reports_changes_and_restricts_mode(tmp_path):
    assert filebrowser.write_config(tmp_path, "a: 1\n") is True
    path = filebrowser.config_path(tmp_path)
    assert path.read_text(encoding="utf-8") == "a: 1\n"
    if os.name == "posix":
        assert path.stat().st_mode & 0o777 == 0o600
    assert filebrowser.write_config(tmp_path, "a: 1\n") is False
    assert filebrowser.write_config(tmp_path, "a: 2\n") is True
    assert list(path.parent.iterdir()) == [path]


@pytest.mark.skipif(not hasattr(os, "geteuid") or os.geteuid() != 0, reason="needs root")
def test_write_config_gives_the_volume_folder_to_filebrowser(tmp_path):
    folder = filebrowser.config_path(tmp_path).parent
    folder.mkdir()  # as Docker creates it: owned by root
    filebrowser.write_config(tmp_path, "a: 1\n")
    assert (folder.stat().st_uid, folder.stat().st_gid) == (1000, 1000)
    assert filebrowser.config_path(tmp_path).stat().st_uid == 1000

    os.chown(folder, 0, 0)  # an install made before this fix heals on the next write
    assert filebrowser.write_config(tmp_path, "a: 1\n") is False
    assert folder.stat().st_uid == 1000


def test_laptop_only_hostname_renders_plain_http_urls():
    text = render(REPO_TEMPLATE.read_text(encoding="utf-8"), hostname="spark.localhost")
    assert 'externalUrl: "http://spark.localhost/files"' in text
    assert 'logoutRedirectUrl: "http://spark.localhost/cosmos-ui/logout"' in text
    assert filebrowser.redirect_uri("spark.localhost") == (
        "http://spark.localhost/files/api/auth/oidc/callback"
    )
