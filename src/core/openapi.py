"""The API contract for the frontend: docs/openapi.json, generated from the FastAPI app.

Regenerate after any API change:  uv run python -m src.core.openapi
tests/core/test_openapi.py fails while the committed file is out of date.
"""

import json
import os
from pathlib import Path
from typing import Any

# The app refuses to start without its secrets; the contract does not depend on them, so the
# generator brings placeholders (never used for anything else).
os.environ.setdefault("JWT_SECRET_KEY", "openapi-generator-placeholder-secret-32-bytes")
os.environ.setdefault("AUTH_HASH_SECRET", "openapi-generator-placeholder-secret-32-bytes")

CONTRACT_PATH = Path(__file__).resolve().parents[2] / "docs" / "openapi.json"


def build_contract() -> dict[str, Any]:
    from src.main import create_app  # imported lazily: the app imports every module

    return create_app().openapi()


def render_contract(contract: dict[str, Any]) -> str:
    return json.dumps(contract, indent=2, ensure_ascii=False) + "\n"


def main() -> None:
    CONTRACT_PATH.write_text(render_contract(build_contract()), encoding="utf-8")
    print(f"wrote {CONTRACT_PATH}")


if __name__ == "__main__":
    main()
