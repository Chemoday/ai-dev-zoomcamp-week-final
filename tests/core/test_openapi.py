"""The committed API contract (docs/openapi.json) matches the app and describes real errors."""

import json

import pytest

from src.core.openapi import CONTRACT_PATH, build_contract, render_contract

HTTP_METHODS = {"get", "post", "put", "patch", "delete"}


@pytest.fixture(scope="module")
def contract() -> dict:
    return build_contract()


def operations(contract: dict):
    for path, item in contract["paths"].items():
        for method, operation in item.items():
            if method in HTTP_METHODS:
                yield path, method, operation


def test_committed_contract_is_up_to_date(contract: dict) -> None:
    committed = CONTRACT_PATH.read_text(encoding="utf-8") if CONTRACT_PATH.exists() else ""

    assert committed == render_contract(contract), (
        "docs/openapi.json is out of date: run `uv run python -m src.core.openapi` and commit it"
    )


def test_fastapi_default_error_shape_is_never_documented(contract: dict) -> None:
    assert "HTTPValidationError" not in json.dumps(contract)


def test_every_error_response_uses_the_shared_error_schema(contract: dict) -> None:
    for path, method, operation in operations(contract):
        for status, response in operation["responses"].items():
            if status.startswith(("4", "5")):
                schema = response["content"]["application/json"]["schema"]
                assert schema == {"$ref": "#/components/schemas/ErrorResponse"}, (method, path)


def test_api_endpoints_document_auth_and_validation_errors(contract: dict) -> None:
    authenticated = 0
    for path, method, operation in operations(contract):
        if not path.startswith("/api/v1/"):
            continue
        responses = operation["responses"]
        if operation.get("security"):  # every authenticated endpoint can answer 401
            authenticated += 1
            assert "401" in responses, (method, path)
        assert "500" in responses, (method, path)
        if operation.get("parameters") or operation.get("requestBody"):
            assert "VALIDATION_ERROR" in responses["422"]["description"], (method, path)
    assert authenticated >= 15, "FastAPI marks authenticated operations with `security`"


def test_meter_values_are_strings_in_the_contract(contract: dict) -> None:
    schemas = contract["components"]["schemas"]

    submitted = schemas["ReadingSubmitRequest"]["properties"]["value"]
    returned = schemas["ReadingResponse"]["properties"]["value"]

    assert submitted["type"] == returned["type"] == "string"
    assert submitted["pattern"].startswith("^") and submitted["pattern"].endswith("$")
