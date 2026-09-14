"""The committed openapi.json is what the frontend's typed client is generated
from. If it drifts from the live app, the frontend is typed against a lie."""

import json

from secondbrain.main import create_app
from secondbrain.openapi import DEFAULT_OUTPUT


def test_committed_openapi_matches_app() -> None:
    committed = json.loads(DEFAULT_OUTPUT.read_text(encoding="utf-8"))
    live = create_app().openapi()
    assert committed == live, (
        "apps/api/openapi.json is stale — run `secondbrain-openapi`, "
        "then `npm run generate:api` in apps/web, and commit both"
    )
