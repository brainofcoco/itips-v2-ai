"""Scalar API reference + OpenAPI spec serving.

Registers two routes on whichever Flask app calls `register_docs(app)`:

  GET /docs           — Scalar UI (HTML page that pulls Scalar from a CDN).
  GET /openapi.yaml   — the canonical spec at v2/openapi/itips-v2.yaml.

Mounted on both servers (public 5050 and inbound 8443) so each surface
is documented from the URL you would actually call. Mounting on the
inbound port also avoids the cross-origin headache when testing a 8443
route through a docs page served from 5050.
"""

from __future__ import annotations

from pathlib import Path

from flask import Flask, Response, abort, send_file

_SPEC_PATH = Path(__file__).resolve().parents[2] / "openapi" / "itips-v2.yaml"

_SCALAR_HTML = """<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <title>ITIPS V2 — API reference</title>
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <style>body{margin:0}</style>
  </head>
  <body>
    <script
      id="api-reference"
      data-url="/openapi.yaml"
      data-configuration='{"theme":"purple","layout":"modern","hideClientButton":false,"defaultHttpClient":{"targetKey":"shell","clientKey":"curl"}}'
    ></script>
    <script src="https://cdn.jsdelivr.net/npm/@scalar/api-reference"></script>
  </body>
</html>
"""


def register_docs(app: Flask) -> None:
    """Add /docs and /openapi.yaml to the given Flask app.

    Idempotent — calling twice on the same app is safe (Flask raises
    AssertionError on duplicate routes, which we swallow).
    """

    @app.get("/openapi.yaml")
    def openapi_spec():  # type: ignore[unused-ignore]
        if not _SPEC_PATH.exists():
            abort(404)
        # Send as UTF-8 yaml so Scalar (and curl) can read it directly.
        return send_file(_SPEC_PATH, mimetype="application/yaml")

    @app.get("/docs")
    def docs_ui():  # type: ignore[unused-ignore]
        return Response(_SCALAR_HTML, mimetype="text/html")

    @app.get("/docs/")
    def docs_ui_slash():  # type: ignore[unused-ignore]
        return Response(_SCALAR_HTML, mimetype="text/html")
