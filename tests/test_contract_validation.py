"""Response Contract validation shared by Monitor and Bug Oracle."""


def _document():
    return {
        "openapi": "3.0.3",
        "info": {"title": "Contract", "version": "1"},
        "paths": {
            "/items": {
                "get": {
                    "responses": {
                        "2XX": {
                            "description": "ok",
                            "headers": {"X-Count": {"schema": {"type": "string", "pattern": "^[0-9]+$"}}},
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "required": ["id"],
                                        "properties": {"id": {"type": "integer"}},
                                    }
                                }
                            },
                        }
                    }
                }
            }
        },
    }


def test_validator_checks_wildcard_body_and_present_declared_headers() -> None:
    """One decoded response reports body and actual header value mismatches."""

    from restscope.api_behavior_monitor.contract_validation import (
        ContractValidator,
        decode_response_evidence,
    )

    evidence = decode_response_evidence(
        status_code=201,
        headers={"Content-Type": "application/json", "X-Count": "many"},
        body=b'{"id":"wrong"}',
    )
    result = ContractValidator().validate(
        document=_document(),
        operation_path="/items",
        operation_method="GET",
        evidence=evidence,
    )

    assert [item.code for item in result.mismatches] == [
        "schema_validation_failed",
        "schema_validation_failed",
    ]
    assert {item.instance_pointer for item in result.mismatches} == {
        "/body/id",
        "/headers/x-count",
    }


def test_missing_declared_response_header_is_not_a_mismatch() -> None:
    """OpenAPI response headers have no required presence semantics."""

    from restscope.api_behavior_monitor.contract_validation import ContractValidator, decode_response_evidence

    result = ContractValidator().validate(
        document=_document(),
        operation_path="/items",
        operation_method="GET",
        evidence=decode_response_evidence(
            status_code=200,
            headers={"content-type": "application/json"},
            body=b'{"id":1}',
        ),
    )

    assert result.matched is True


def test_json_null_is_validated_instead_of_being_confused_with_no_decoded_value() -> None:
    """A JSON null body still violates an object response Schema."""

    from restscope.api_behavior_monitor.contract_validation import ContractValidator, decode_response_evidence

    result = ContractValidator().validate(
        document=_document(),
        operation_path="/items",
        operation_method="GET",
        evidence=decode_response_evidence(
            status_code=200,
            headers={"content-type": "application/json"},
            body=b"null",
        ),
    )

    assert [item.code for item in result.mismatches] == ["schema_validation_failed"]
