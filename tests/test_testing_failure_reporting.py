from __future__ import annotations


def test_json_failure_messages_follow_priority_and_keep_case_associations() -> None:
    from restscope.testing.failure_reporting import (
        FailureCaseEvidence,
        build_batch_failure_report,
    )

    report = build_batch_failure_report(
        [
            FailureCaseEvidence(
                case_id="case_1",
                status_code=400,
                reason_phrase="Bad Request",
                media_type="application/json",
                body=(
                    b'{"message":" Project   not found ",'
                    b'"detail":"ignored","requestId":"one"}'
                ),
            ),
            FailureCaseEvidence(
                case_id="case_2",
                status_code=400,
                reason_phrase="Bad Request",
                media_type="application/problem+json",
                body=b'{"message":"Project not found","requestId":"two"}',
            ),
            FailureCaseEvidence(
                case_id="case_3",
                status_code=422,
                reason_phrase="Unprocessable Entity",
                media_type="application/json",
                body=(
                    b'{"errors":['
                    b'{"field":"name","message":"is required"},'
                    b'{"field":"status","detail":"unsupported value"}'
                    b"]}"
                ),
            ),
            FailureCaseEvidence(
                case_id="case_4",
                status_code=400,
                reason_phrase="Bad Request",
                media_type="application/json",
                body=b'{"message":"project not found"}',
            ),
        ]
    )

    assert report.model_dump(mode="json") == {
        "unique_failure_messages": [
            {
                "failure_id": "f1",
                "message": "HTTP 400: Project not found",
                "case_ids": ["case_1", "case_2"],
            },
            {
                "failure_id": "f2",
                "message": "HTTP 422: name: is required",
                "case_ids": ["case_3"],
            },
            {
                "failure_id": "f3",
                "message": "HTTP 422: status: unsupported value",
                "case_ids": ["case_3"],
            },
            {
                "failure_id": "f4",
                "message": "HTTP 400: project not found",
                "case_ids": ["case_4"],
            },
        ],
        "truncated": False,
    }


def test_nested_error_text_transport_and_fallback_messages_are_deterministic() -> None:
    from restscope.testing.failure_reporting import (
        FailureCaseEvidence,
        build_batch_failure_report,
    )

    report = build_batch_failure_report(
        [
            FailureCaseEvidence(
                case_id="nested",
                status_code=409,
                reason_phrase="Conflict",
                media_type="application/json",
                body=b'{"error":{"code":"CONFLICT","message":"already\\n exists"}}',
            ),
            FailureCaseEvidence(
                case_id="text",
                status_code=503,
                reason_phrase="Service Unavailable",
                media_type="text/plain; charset=utf-8",
                body=b"  try\t again\r\nlater  ",
            ),
            FailureCaseEvidence(
                case_id="invalid_json",
                status_code=500,
                reason_phrase="Internal Server Error",
                media_type="application/json",
                body=b"{not-json",
            ),
            FailureCaseEvidence(
                case_id="truncated_json",
                status_code=500,
                reason_phrase="Internal Server Error",
                media_type="application/json",
                body=b'{"message":"partial',
                body_truncated=True,
            ),
            FailureCaseEvidence(
                case_id="binary",
                status_code=502,
                reason_phrase="Bad Gateway",
                media_type="text/plain",
                body=b"\x00\x01\x02",
            ),
            FailureCaseEvidence(
                case_id="transport",
                transport_error_code="request_timeout",
                transport_error_message="HTTP request timed out",
            ),
        ]
    )

    assert [item.message for item in report.unique_failure_messages] == [
        "HTTP 409: already exists",
        "HTTP 503: try again later",
        "HTTP 500 Internal Server Error",
        "HTTP 500 Internal Server Error [failure body truncated]",
        "HTTP 502 Bad Gateway",
        "TRANSPORT request_timeout: HTTP request timed out",
    ]


def test_failure_report_ignores_2xx_and_bounds_messages_and_unique_count() -> None:
    from restscope.testing.failure_reporting import (
        MAX_FAILURE_MESSAGE_BYTES,
        MAX_UNIQUE_FAILURE_MESSAGES,
        FailureCaseEvidence,
        build_batch_failure_report,
    )

    cases = [
        FailureCaseEvidence(
            case_id="success",
            status_code=200,
            reason_phrase="OK",
            media_type="text/plain",
            body=b"not a failure",
        ),
        FailureCaseEvidence(
            case_id="long",
            status_code=400,
            reason_phrase="Bad Request",
            media_type="text/plain",
            body=b"x" * (MAX_FAILURE_MESSAGE_BYTES * 2),
        ),
        *[
            FailureCaseEvidence(
                case_id=f"case_{index}",
                status_code=400,
                reason_phrase="Bad Request",
                media_type="application/json",
                body=(f'{{"message":"message {index}"}}').encode(),
            )
            for index in range(MAX_UNIQUE_FAILURE_MESSAGES + 5)
        ],
    ]

    report = build_batch_failure_report(cases)

    assert len(report.unique_failure_messages) == MAX_UNIQUE_FAILURE_MESSAGES
    assert report.truncated is True
    long_message = report.unique_failure_messages[0].message
    assert len(long_message.encode("utf-8")) <= MAX_FAILURE_MESSAGE_BYTES
    assert long_message.endswith("…[failure message truncated]")


def test_empty_failure_report_is_part_of_the_public_execution_contract() -> None:
    from restscope.testing import BatchFailureReport, OperationExecutionReport

    report = OperationExecutionReport(
        run_id="run",
        operation_key="GET /items",
        seed=1,
        config_revision=1,
        status="completed",
        cases=[],
        status_code_counts={},
        error_count=0,
        observed_2xx=False,
        failure_report=BatchFailureReport(),
    )

    assert report.failure_report.unique_failure_messages == []
    assert report.failure_report.truncated is False
