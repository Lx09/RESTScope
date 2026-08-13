"""Single-pass response decoding shared by pipeline Monitor modules."""


def test_json_response_is_normalized_and_decoded_once() -> None:
    """Observation publishes lowercase headers and one detached JSON value."""

    from restscope.api_behavior_monitor.response_evidence import (
        decode_response_evidence,
    )

    evidence = decode_response_evidence(
        status_code=201,
        headers={"Content-Type": "Application/JSON; charset=utf-8", "X-Test": "yes"},
        body=b'{"id":1}',
    )

    assert evidence.media_type == "application/json"
    assert evidence.headers == {
        "content-type": "Application/JSON; charset=utf-8",
        "x-test": "yes",
    }
    assert evidence.body_kind == "json"
    assert evidence.json_value == {"id": 1}


def test_invalid_json_remains_bytes_without_a_second_parser_result() -> None:
    """Invalid declared JSON is explicit evidence for Contract Monitor retry."""

    from restscope.api_behavior_monitor.response_evidence import (
        decode_response_evidence,
    )

    evidence = decode_response_evidence(
        status_code=500,
        headers={"content-type": "application/json"},
        body=b"{",
    )

    assert evidence.body_kind == "invalid_json"
    assert evidence.json_value is None
    assert evidence.body == b"{"
