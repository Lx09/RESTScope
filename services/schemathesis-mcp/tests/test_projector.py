"""Tests for projecting Schemathesis NDJSON records into MCP events."""

from schemathesis_mcp.projector import NdjsonProjector


def test_scenario_finished_is_compacted_and_failures_are_extracted() -> None:
    record = {
        "ScenarioFinished": {
            "timestamp": 2.0,
            "phase": "Fuzzing",
            "status": "failure",
            "elapsed_time": 0.2,
            "recorder": {
                "label": "POST /users",
                "cases": {
                    "parent": {"value": {"id": "parent", "method": "POST", "path": "/users"}},
                    "child": {
                        "value": {"id": "child", "method": "GET", "path": "/users/1"},
                        "parent_id": "parent",
                    },
                },
                "checks": {
                    "child": [
                        {
                            "name": "not_a_server_error",
                            "status": "failure",
                            "failure_info": {"failure": {"type": "ServerError", "message": "Server error"}},
                        }
                    ]
                },
                "interactions": {
                    "child": {
                        "request": {
                            "method": "GET",
                            "uri": "https://api.example/users/1?token=secret",
                            "headers": {"Authorization": ["Bearer secret"]},
                        },
                        "response": {"status_code": 500, "headers": {}, "content": {"$base64": "YmFk"}},
                    }
                },
            },
        }
    }

    [event] = NdjsonProjector().project(record)

    assert event["type"] == "scenario_finished"
    assert event["operation"] == "POST /users"
    assert event["failures"] == 1
    assert "recorder" not in event
    [failure] = event["_failures"]
    assert failure["check"] == "not_a_server_error"
    assert failure["request"]["headers"]["Authorization"] == "[REDACTED]"
    assert "%5BREDACTED%5D" in failure["request"]["uri"]
    assert "secret" not in failure["curl"]
    assert failure["curl"].startswith("curl -X GET")
    assert [case["id"] for case in failure["related_cases"]] == ["parent", "child"]


def test_loading_and_engine_events_have_stable_shapes() -> None:
    projector = NdjsonProjector()

    [loading] = projector.project(
        {
            "LoadingFinished": {
                "timestamp": 1.0,
                "specification": {"kind": "openapi", "version": "3.1.0"},
                "statistic": {"operations": {"total": 2, "selected": 1}},
            }
        }
    )
    [finished] = projector.project(
        {"EngineFinished": {"timestamp": 2.0, "running_time": 1.0, "stop_reason": "completed"}}
    )

    assert loading == {
        "type": "loading_finished",
        "timestamp": 1.0,
        "specification": {"kind": "openapi", "version": "3.1.0"},
        "operations": {"total": 2, "selected": 1},
    }
    assert finished["type"] == "engine_finished"
    assert finished["stop_reason"] == "completed"


def test_phase_and_error_events_are_compacted() -> None:
    projector = NdjsonProjector()

    [phase] = projector.project({"PhaseStarted": {"timestamp": 1.0, "phase": {"name": "Fuzzing", "is_enabled": True}}})
    [error] = projector.project(
        {
            "NonFatalError": {
                "timestamp": 2.0,
                "phase": "Fuzzing",
                "label": "GET /users",
                "value": {"type": "NetworkError", "message": "Connection refused"},
            }
        }
    )

    assert phase == {"type": "phase_started", "timestamp": 1.0, "phase": "Fuzzing"}
    assert error["message"] == "Connection refused"
