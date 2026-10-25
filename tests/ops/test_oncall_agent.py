"""On-call agent (scripts/oncall_agent.py): parsing, enrichment and the responder flow, offline."""

import json
import subprocess
from pathlib import Path

from scripts.oncall_agent import (
    Incident,
    build_prompt,
    enrich,
    handle_alert_payload,
    load_incident,
    parse_alerts,
    project_frames,
    respond,
)

TRACE_ID = "7acac0e97c592f11a72387d9e57d18b4"
TRACEBACK = """Traceback (most recent call last):
  File "/usr/local/lib/python3.12/site-packages/starlette/routing.py", line 73, in app
    response = await f(request)
  File "/app/src/modules/meters/router.py", line 61, in submit_reading
    return await service.submit_reading(user, body)
  File "/app/src/modules/meters/service.py", line 192, in _submit
    previous_value = previous_value / 0
decimal.DivisionByZero: [<class 'decimal.DivisionByZero'>]
"""
GRAFANA_PAYLOAD = {
    "receiver": "oncall-agent",
    "status": "firing",
    "alerts": [
        {
            "status": "firing",
            "labels": {"alertname": "Meter reading submission fails (5xx)", "trace_id": TRACE_ID},
            "annotations": {"summary": f"POST /api/v1/meters/readings returned 5xx ({TRACE_ID})"},
            "startsAt": "2026-10-25T10:40:00Z",
        },
        {"status": "resolved", "labels": {"alertname": "x", "trace_id": "old"}},
        {"status": "firing", "labels": {"alertname": "no trace"}},
    ],
}
LOG_RECORDS = [
    {"event": "unhandled_exception", "trace_id": TRACE_ID, "exception": TRACEBACK},
    {"event": "http_request", "trace_id": TRACE_ID, "status_code": 500},
]


def test_only_firing_alerts_with_a_trace_id_become_incidents() -> None:
    (incident,) = parse_alerts(GRAFANA_PAYLOAD)

    assert incident.trace_id == TRACE_ID
    assert incident.alertname == "Meter reading submission fails (5xx)"
    assert incident.starts_at == "2026-10-25T10:40:00Z"


def test_the_offending_frame_is_the_innermost_one_in_our_code() -> None:
    frames = project_frames(TRACEBACK)
    incident = enrich(Incident(trace_id=TRACE_ID, alertname="a"), LOG_RECORDS)

    assert [f.path for f in frames] == [
        "src/modules/meters/router.py",
        "src/modules/meters/service.py",
    ]
    assert (incident.file, incident.line, incident.function) == (
        "src/modules/meters/service.py",
        192,
        "_submit",
    )
    assert incident.error_type == "decimal.DivisionByZero"
    assert "previous_value / 0" in incident.error_log


def test_each_trace_id_is_delivered_once_with_its_correlated_logs() -> None:
    delivered: list[Incident] = []
    fetched: list[str] = []
    seen: set[str] = set()

    def fetch(trace_id: str) -> list[dict]:
        fetched.append(trace_id)
        return LOG_RECORDS

    for _ in range(2):  # Grafana re-sends a group on every group_interval
        handle_alert_payload(GRAFANA_PAYLOAD, fetch, delivered.append, seen)

    assert fetched == [TRACE_ID]
    assert [i.file for i in delivered] == ["src/modules/meters/service.py"]


def test_incident_files_round_trip_and_dispatch_events_are_understood(tmp_path: Path) -> None:
    incident = enrich(Incident(trace_id=TRACE_ID, alertname="a"), LOG_RECORDS)
    plain = tmp_path / "incident.json"
    event = tmp_path / "event.json"
    plain.write_text(json.dumps(incident.to_payload()))
    event.write_text(json.dumps({"client_payload": {"incident": incident.to_payload()}}))

    assert load_incident(plain).line == load_incident(event).line == 192


def test_the_prompt_carries_the_alert_context() -> None:
    prompt = build_prompt(enrich(Incident(trace_id=TRACE_ID, alertname="a"), LOG_RECORDS))

    assert TRACE_ID in prompt
    assert "src/modules/meters/service.py:192" in prompt
    assert "regression test" in prompt


class FakeRunner:
    def __init__(self, failing: str | None = None) -> None:
        self.commands: list[list[str]] = []
        self.failing = failing

    def __call__(self, command) -> subprocess.CompletedProcess:
        self.commands.append(list(command))
        code = 1 if command[0] == self.failing or command[:3] == ["uv", "run", self.failing] else 0
        return subprocess.CompletedProcess(command, code, stdout="406 passed in 30s\n", stderr="")


def test_green_tests_open_a_pull_request_that_references_the_alert() -> None:
    runner = FakeRunner()
    incident = enrich(Incident(trace_id=TRACE_ID, alertname="Meter reading fails"), LOG_RECORDS)

    assert respond(incident, runner) == 0

    programs = [c[0] if c[0] != "git" else f"git {c[1]}" for c in runner.commands]
    assert programs == ["claude", "uv", "git checkout", "git add", "git commit", "git push", "gh"]
    pr = runner.commands[-1]
    body = pr[pr.index("--body") + 1]
    assert TRACE_ID in body and "Meter reading fails" in body and "406 passed" in body


def test_red_tests_open_no_pull_request() -> None:
    runner = FakeRunner(failing="pytest")

    assert respond(enrich(Incident(trace_id=TRACE_ID, alertname="a"), LOG_RECORDS), runner) == 1
    assert [c[0] for c in runner.commands] == ["claude", "uv"]
