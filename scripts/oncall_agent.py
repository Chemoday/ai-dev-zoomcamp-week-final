"""On-call responder agent (docs/TASKS.md, Task 8; docs/OBSERVABILITY.md).

Two commands, standard library only, so it runs both next to the local stack and in CI:

  serve    Webhook receiver for the Grafana alert (ops/alert-rules.yaml). For every firing alert it
           takes the trace_id, fetches the correlated error log from Loki, finds the offending
           file and line in the traceback, and hands the incident on: as a GitHub
           `repository_dispatch` ("oncall-alert") when GITHUB_TOKEN and GITHUB_REPOSITORY are set,
           otherwise as incidents/<trace_id>.json.

  respond  Runs in the checked-out repository (.github/workflows/oncall-responder.yml). It asks
           Claude Code to add a regression test and fix the bug, runs pytest, and opens a pull
           request that references the alert. No pull request when pytest stays red.

Logs reaching this agent are already PII-scrubbed by the backend and the collector.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.parse
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SERVICE_NAME = "apartment-platform-backend"
DISPATCH_EVENT = "oncall-alert"
MAX_ERROR_LOG_CHARS = 20_000  # repository_dispatch payloads are limited in size
# "File "/app/src/modules/meters/service.py", line 192, in _submit"
_FRAME_RE = re.compile(r'File "(?P<path>[^"]+)", line (?P<line>\d+), in (?P<function>\S+)')
# The final "Type: message" line of a traceback (not indented, a dotted Python name).
_EXCEPTION_LINE_RE = re.compile(r"^(?P<type>[A-Za-z_][\w.]*)(?::|$)")


# --- Incidents ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Frame:
    path: str  # relative to the repository root, e.g. "src/modules/meters/service.py"
    line: int
    function: str


@dataclass
class Incident:
    trace_id: str
    alertname: str
    summary: str = ""
    starts_at: str = ""
    error_type: str = ""
    file: str = ""
    line: int = 0
    function: str = ""
    error_log: str = ""
    frames: list[Frame] = field(default_factory=list)

    def to_payload(self) -> dict:
        payload = asdict(self)
        payload.pop("frames")
        payload["error_log"] = self.error_log[-MAX_ERROR_LOG_CHARS:]
        return payload


def parse_alerts(payload: dict) -> list[Incident]:
    """Firing alerts of a Grafana webhook payload that carry a trace_id."""
    incidents = []
    for alert in payload.get("alerts", []):
        labels = alert.get("labels", {})
        if alert.get("status") != "firing" or not labels.get("trace_id"):
            continue
        incidents.append(
            Incident(
                trace_id=labels["trace_id"],
                alertname=labels.get("alertname", "unknown alert"),
                summary=alert.get("annotations", {}).get("summary", ""),
                starts_at=alert.get("startsAt", ""),
            )
        )
    return incidents


def project_frames(traceback_text: str) -> list[Frame]:
    """Traceback frames inside this repository's src/, with container paths made relative."""
    frames = []
    for match in _FRAME_RE.finditer(traceback_text):
        path = match["path"].replace("\\", "/")
        if "/site-packages/" in path or "/src/" not in f"/{path}":
            continue
        relative = "src/" + path.split("/src/", 1)[1] if "/src/" in path else path
        frames.append(Frame(relative, int(match["line"]), match["function"]))
    return frames


def exception_type(traceback_text: str) -> str:
    for line in reversed(traceback_text.strip().splitlines()):
        if line and not line[0].isspace() and not line.startswith("Traceback"):
            match = _EXCEPTION_LINE_RE.match(line)
            return match["type"] if match else ""
    return ""


def error_log_of(records: Sequence[dict]) -> str:
    """The traceback of the first record that has one, else all records as JSON lines."""
    for record in records:
        if record.get("exception"):
            return str(record["exception"])
    return "\n".join(json.dumps(record, sort_keys=True) for record in records)


def enrich(incident: Incident, records: Sequence[dict]) -> Incident:
    incident.error_log = error_log_of(records)
    incident.error_type = exception_type(incident.error_log)
    incident.frames = project_frames(incident.error_log)
    if incident.frames:  # the innermost frame in our code is where the error surfaced
        offending = incident.frames[-1]
        incident.file, incident.line, incident.function = (
            offending.path,
            offending.line,
            offending.function,
        )
    return incident


# --- Loki and GitHub (network) --------------------------------------------------------------------


def _http_json(request: urllib.request.Request, timeout: float = 10) -> dict:
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 (fixed URLs)
        body = response.read()
    return json.loads(body) if body else {}


def fetch_correlated_logs(loki_url: str, trace_id: str, lookback: timedelta) -> list[dict]:
    """Log records of the backend that carry this trace_id (oldest first)."""
    now = datetime.now(UTC)
    query = {
        "query": f'{{service_name="{SERVICE_NAME}"}} |= "{trace_id}"',
        "start": str(int((now - lookback).timestamp() * 1e9)),
        "end": str(int(now.timestamp() * 1e9)),
        "limit": "100",
        "direction": "forward",
    }
    url = f"{loki_url.rstrip('/')}/loki/api/v1/query_range?{urllib.parse.urlencode(query)}"
    data = _http_json(urllib.request.Request(url))
    records = []
    for stream in data.get("data", {}).get("result", []):
        for _timestamp, line in stream.get("values", []):
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                records.append({"line": line})
    return records


def dispatch_to_github(repository: str, token: str, incident: Incident) -> None:
    request = urllib.request.Request(
        f"https://api.github.com/repos/{repository}/dispatches",
        method="POST",
        data=json.dumps(
            {"event_type": DISPATCH_EVENT, "client_payload": {"incident": incident.to_payload()}}
        ).encode(),
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    _http_json(request)


def handle_alert_payload(
    payload: dict,
    fetch_logs: Callable[[str], list[dict]],
    deliver: Callable[[Incident], None],
    seen: set[str],
) -> list[Incident]:
    """Enrich and deliver every new firing incident (one per trace_id, even if re-sent)."""
    delivered = []
    for incident in parse_alerts(payload):
        if incident.trace_id in seen:
            continue
        seen.add(incident.trace_id)
        deliver(enrich(incident, fetch_logs(incident.trace_id)))
        delivered.append(incident)
    return delivered


def serve(port: int, loki_url: str, incidents_dir: Path) -> None:
    repository, token = os.environ.get("GITHUB_REPOSITORY"), os.environ.get("GITHUB_TOKEN")
    seen: set[str] = set()

    def deliver(incident: Incident) -> None:
        if repository and token:
            dispatch_to_github(repository, token, incident)
            target = f"repository_dispatch on {repository}"
        else:
            incidents_dir.mkdir(parents=True, exist_ok=True)
            path = incidents_dir / f"{incident.trace_id}.json"
            path.write_text(json.dumps(incident.to_payload(), indent=2))
            target = str(path)
        _log("incident_delivered", trace_id=incident.trace_id, file=incident.file, to=target)

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 (http.server API)
            length = int(self.headers.get("Content-Length", 0))
            try:
                payload = json.loads(self.rfile.read(length) or b"{}")
                handle_alert_payload(
                    payload,
                    lambda trace_id: fetch_correlated_logs(loki_url, trace_id, timedelta(hours=1)),
                    deliver,
                    seen,
                )
                self.send_response(202)
            except Exception as error:  # the alert sender only needs to know it failed
                _log("alert_handling_failed", error=type(error).__name__, detail=str(error))
                self.send_response(500)
            self.end_headers()

        def do_GET(self) -> None:  # noqa: N802 (health check)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, *args: object) -> None:  # silence the default access log
            pass

    _log("oncall_agent_listening", port=port, loki=loki_url, github=bool(repository and token))
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()


# --- Responder (runs in CI) -----------------------------------------------------------------------

type Runner = Callable[[Sequence[str]], subprocess.CompletedProcess]


def run(command: Sequence[str]) -> subprocess.CompletedProcess:
    return subprocess.run(command, capture_output=True, text=True, check=False)


def build_prompt(incident: Incident) -> str:
    location = f"{incident.file}:{incident.line} in {incident.function}" if incident.file else "?"
    return f"""You are the on-call responder of this repository (read AGENTS.md first).
A user-impact alert fired in production-like monitoring:

- Alert: {incident.alertname}
- Summary: {incident.summary}
- trace_id: {incident.trace_id}
- Error: {incident.error_type or "unknown"} at {location}

Correlated error log for this trace_id (already PII-scrubbed):
```
{incident.error_log[-MAX_ERROR_LOG_CHARS:]}
```

Do exactly this:
1. Find the root cause starting at {location}.
2. Add a regression test under tests/ that reproduces the failure (it must fail without the fix).
3. Fix the bug with the smallest correct change. Keep the AGENTS.md guardrails.
4. Run `uv run pytest -q` until it is green. Do not commit, push or open a pull request.
"""


def pull_request_body(incident: Incident, pytest_summary: str) -> str:
    return f"""Automated fix by the on-call responder agent for the alert **{incident.alertname}**.

| | |
| :--- | :--- |
| trace_id | `{incident.trace_id}` |
| Alert started | {incident.starts_at or "unknown"} |
| Summary | {incident.summary} |
| Error | `{incident.error_type or "unknown"}` |
| Location | `{incident.file}:{incident.line}` (`{incident.function}`) |

The agent added a regression test and a fix; `pytest`: `{pytest_summary}`.
Review it like any other change before merging. Runbook: docs/OBSERVABILITY.md.

<details><summary>Correlated error log</summary>

```
{incident.error_log[-4000:]}
```
</details>

🤖 Generated with [Claude Code](https://claude.com/claude-code)
"""


def respond(incident: Incident, runner: Runner = run, base: str = "main") -> int:
    """Fix, test, and open a pull request. Returns the process exit code."""
    claude = runner(
        [
            "claude",
            "-p",
            build_prompt(incident),
            "--allowedTools",
            "Read,Edit,Write,Glob,Grep,Bash(uv run pytest:*),Bash(uv run ruff:*)",
        ]
    )
    if claude.returncode != 0:
        _log("claude_failed", stderr=claude.stderr[-2000:])
        return 1

    tests = runner(["uv", "run", "pytest", "-q"])
    summary = (tests.stdout.strip().splitlines() or ["no output"])[-1]
    if tests.returncode != 0:
        _log("pytest_red_no_pull_request", summary=summary)
        return 1

    branch = f"oncall/{incident.trace_id[:12]}"
    title = (
        f"fix: on-call fix for {incident.error_type or 'a 5xx'} (trace {incident.trace_id[:12]})"
    )
    for command in (
        ["git", "checkout", "-b", branch],
        ["git", "add", "-A"],
        [
            "git",
            "commit",
            "-m",
            f"{title}\n\nAlert: {incident.alertname}\ntrace_id: {incident.trace_id}",
        ],
        ["git", "push", "-u", "origin", branch],
        [
            "gh",
            "pr",
            "create",
            "--base",
            base,
            "--head",
            branch,
            "--title",
            title,
            "--body",
            pull_request_body(incident, summary),
        ],
    ):
        step = runner(command)
        if step.returncode != 0:
            _log("step_failed", command=command[:3], stderr=step.stderr[-2000:])
            return 1
    _log("pull_request_opened", branch=branch, pytest=summary)
    return 0


# --- CLI ------------------------------------------------------------------------------------------


def _log(event: str, **fields: object) -> None:
    print(json.dumps({"event": event, **fields}, default=str), flush=True)


def load_incident(path: Path) -> Incident:
    """An incident file: either serve's output, or a repository_dispatch event payload."""
    data = json.loads(path.read_text())
    data = data.get("client_payload", data).get("incident", data)
    return Incident(**{key: value for key, value in data.items() if key != "frames"})


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    serve_cmd = commands.add_parser("serve", help="receive Grafana alert webhooks")
    serve_cmd.add_argument("--port", type=int, default=8088)
    serve_cmd.add_argument("--loki-url", default=os.environ.get("LOKI_URL", "http://lgtm:3100"))
    serve_cmd.add_argument("--incidents-dir", type=Path, default=Path("incidents"))
    respond_cmd = commands.add_parser("respond", help="fix an incident and open a pull request")
    respond_cmd.add_argument("--incident", type=Path, required=True)
    respond_cmd.add_argument("--base", default="main")
    args = parser.parse_args(argv)

    if args.command == "serve":
        serve(args.port, args.loki_url, args.incidents_dir)
        return 0
    return respond(load_incident(args.incident), base=args.base)


if __name__ == "__main__":
    sys.exit(main())
