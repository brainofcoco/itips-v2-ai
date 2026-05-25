"""Scalar /docs UI and /openapi.yaml stay loadable on a Flask app."""

from __future__ import annotations

import pytest
import yaml
from flask import Flask

from itips.api.docs import register_docs


@pytest.fixture
def client():
    app = Flask("itips-test")
    register_docs(app)
    return app.test_client()


def test_docs_route_returns_scalar_html(client):
    response = client.get("/docs")
    assert response.status_code == 200
    assert response.mimetype == "text/html"
    body = response.get_data(as_text=True)
    assert "@scalar/api-reference" in body
    assert '/openapi.yaml' in body


def test_docs_trailing_slash_also_works(client):
    assert client.get("/docs/").status_code == 200


def test_openapi_yaml_is_served(client):
    response = client.get("/openapi.yaml")
    assert response.status_code == 200
    assert "yaml" in response.mimetype
    spec = yaml.safe_load(response.get_data(as_text=True))
    assert spec["openapi"].startswith("3.")
    assert spec["info"]["title"].startswith("ITIPS")


def test_openapi_describes_all_inbound_b_routes(client):
    spec = yaml.safe_load(client.get("/openapi.yaml").get_data(as_text=True))
    paths = spec["paths"]
    for required in (
        "/local/api/v1/personnel/sync",
        "/local/api/v1/config",
        "/local/api/v1/maintenance/window",
        "/local/api/v1/commands",
        "/local/api/v1/firmware/update",
    ):
        assert required in paths, f"OpenAPI spec is missing {required}"


def test_openapi_declares_bearer_security_scheme(client):
    spec = yaml.safe_load(client.get("/openapi.yaml").get_data(as_text=True))
    schemes = spec["components"]["securitySchemes"]
    assert schemes["bearerAuth"]["scheme"] == "bearer"
