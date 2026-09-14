"""Export the OpenAPI schema to a file: the `secondbrain-openapi` entrypoint.

The frontend's typed client is generated from this file (`npm run generate:api`
in apps/web), so it can be regenerated without a running server. Re-run this
after any change to routes or schemas, then regenerate the client, and commit
both — a contract change becomes a TypeScript compile error, not a runtime 422.
"""

import json
import sys
from pathlib import Path

from secondbrain.main import create_app

DEFAULT_OUTPUT = Path(__file__).resolve().parents[2] / "openapi.json"  # apps/api/openapi.json


def main() -> None:
    output = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_OUTPUT
    schema = create_app().openapi()
    output.write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {output} ({len(schema['paths'])} paths)")


if __name__ == "__main__":
    main()
