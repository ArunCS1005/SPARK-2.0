import json

import httpx
import pytest

from spark_cli.docker_api import DockerClient
from spark_cli.errors import SparkError


def docker_with(networks: dict, project: str = "spark"):
    """A DockerClient whose engine knows one container `c1` attached to `networks`."""
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET" and request.url.path == "/containers/c1/json":
            return httpx.Response(200, json={"NetworkSettings": {"Networks": networks}})
        if request.method == "POST" and request.url.path.endswith(("/disconnect", "/connect")):
            return httpx.Response(200)
        raise AssertionError(f"unexpected request {request.method} {request.url.path}")

    http = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://docker")
    return DockerClient(http, project), requests


def test_project_network_prefers_the_projects_default_network():
    docker, _ = docker_with(
        {"other_net": {}, "spark-dev_default": {"Aliases": ["cosmos"]}}, project="spark-dev"
    )
    assert docker.project_network("c1") == ("spark-dev_default", {"Aliases": ["cosmos"]})


def test_project_network_falls_back_to_the_only_network():
    docker, _ = docker_with({"custom": {"Aliases": None}})
    assert docker.project_network("c1") == ("custom", {"Aliases": None})


def test_project_network_refuses_to_guess():
    docker, _ = docker_with({"a": {}, "b": {}})
    with pytest.raises(SparkError, match="Cannot tell which of"):
        docker.project_network("c1")


def test_add_network_alias_reconnects_with_all_aliases():
    docker, requests = docker_with({"spark_default": {"Aliases": ["cosmos", "spark.invalid"]}})
    assert docker.add_network_alias("c1", "cat.trycloudflare.com") is True

    disconnect, connect = requests[-2:]
    assert disconnect.url.path == "/networks/spark_default/disconnect"
    assert json.loads(disconnect.content) == {"Container": "c1"}
    assert connect.url.path == "/networks/spark_default/connect"
    assert json.loads(connect.content) == {
        "Container": "c1",
        "EndpointConfig": {"Aliases": ["cosmos", "spark.invalid", "cat.trycloudflare.com"]},
    }


def test_add_network_alias_leaves_an_existing_alias_alone():
    docker, requests = docker_with({"spark_default": {"Aliases": ["cosmos", "spark.example.com"]}})
    assert docker.add_network_alias("c1", "spark.example.com") is False
    assert [r.method for r in requests] == ["GET"]


def test_add_network_alias_handles_no_aliases():
    docker, requests = docker_with({"spark_default": {"Aliases": None}})
    assert docker.add_network_alias("c1", "spark.example.com") is True
    assert json.loads(requests[-1].content)["EndpointConfig"] == {"Aliases": ["spark.example.com"]}
