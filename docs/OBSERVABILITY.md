# Observability, User-Impact Alerting & On-Call Agent (Task 8)

Everything runs locally with the usual single command, `docker compose up --build`. The VPS deploy
(docs/DEPLOYMENT.md) does not run this stack: the host is shared and small.

```text
backend ──OTLP/HTTP──► otel-collector ──OTLP──► lgtm (Grafana · Tempo · Loki · Prometheus)
 traces, metrics, logs   (2nd PII guard)                  │ alert rule (Loki, per trace_id)
                                                          ▼ webhook
                                              oncall-agent (scripts/oncall_agent.py serve)
                                                 │ fetches the error log by trace_id from Loki
                                                 ▼ repository_dispatch "oncall-alert"
                                   GitHub Actions: oncall-responder.yml
                                     Claude Code: regression test + fix → pytest → pull request
```

| Service | What | Port |
| :--- | :--- | :--- |
| `backend` | Exports traces, metrics and logs when `OTEL_EXPORTER_OTLP_ENDPOINT` is set | `8000` |
| `otel-collector` | `ops/otel-collector-config.yaml`: OTLP in, masks emails and phones, OTLP out | internal |
| `lgtm` | `grafana/otel-lgtm`: Grafana, Tempo (traces), Loki (logs), Prometheus (metrics) | `3000` (`GRAFANA_PORT`) |
| `oncall-agent` | Webhook receiver of the alert | internal `8088` |

## 1. Follow one request across traces, logs and metrics

1. Submit a reading (the demo user "Owner Apt 14", see docs/API.md, section 5) and note the
   `X-Trace-Id` response header.
2. Open Grafana on http://localhost:3000 (local only; sign in as `admin` / `admin` for alerting) → **Explore**:
   - **Tempo**: search the trace id. The trace shows the HTTP server span, `meters.submit_reading`
     and the database work.
   - **Loki**: `{service_name="apartment-platform-backend"} |= "<trace_id>"`. Every log line of
     that request, including `http_request` and, on a failure, `unhandled_exception` with the
     scrubbed traceback. The lines are JSON (`| json` parses them).
   - **Prometheus**: `http_server_duration_milliseconds_count{http_target="/api/v1/meters/readings"}`
     (request count, latency histogram by method and status code).

Each signal carries the same `trace_id`, so one id leads from a user's error message (500 bodies
include `trace_id`) to the trace, the logs and the metrics.

**No PII in telemetry.** The backend scrubs every span, JSON log line and exported log record
(`src/core/telemetry.py`; exported log records keep only the scrubbed JSON body plus logger name
and code location). Metrics carry route templates, methods and status codes only. The collector
masks emails and phone numbers once more. Covered by `tests/core/test_telemetry.py` and
`tests/core/test_telemetry_export.py`.

## 2. The user-impact alert

`ops/alert-rules.yaml` is provisioned into Grafana (**Alerting → Alert rules → Apartment platform
→ "Meter reading submission fails (5xx)"**):

- Query: the `http_request` log lines of `POST /api/v1/meters/readings` with `status_code >= 500`
  in the last 5 minutes, **summed by `trace_id`**. Evaluated every 10 s, fires at once.
- Every failing request becomes its own alert instance, labelled with its `trace_id`.
- Contact point `oncall-agent`: a webhook to `http://oncall-agent:8088/alerts`, grouped by
  `alertname` and `trace_id`.

## 3. The on-call agent

`scripts/oncall_agent.py` (standard library only):

- **`serve`** (the `oncall-agent` container): for each firing alert it fetches the correlated log
  records from Loki by `trace_id`, extracts the error type and the innermost frame in `src/`
  (file, line, function) from the traceback, and hands the incident on:
  - with `ONCALL_GITHUB_TOKEN` set in the shell that runs `docker compose up` (a token allowed to
    create repository dispatch events): `repository_dispatch` `oncall-alert` to the repository;
  - otherwise it writes `incidents/<trace_id>.json` (git-ignored).
  Each `trace_id` is delivered once, even when Grafana re-sends the group.
- **`respond`** (in `.github/workflows/oncall-responder.yml`): asks Claude Code for a regression
  test and the smallest fix (tools limited to reading, editing, `pytest` and `ruff`), runs
  `uv run pytest -q`, and only when it is green commits to `oncall/<trace_id>` and opens a pull
  request with the alert, the `trace_id`, the location and the error log. Red tests: no PR.

Repository secrets for the workflow:

| Secret | Needed | Why |
| :--- | :--- | :--- |
| `ANTHROPIC_API_KEY` **or** `CLAUDE_CODE_OAUTH_TOKEN` | yes | Claude Code credential (API key, or a subscription token from `claude setup-token`) |
| `ONCALL_PR_TOKEN` | optional | A PAT for pushing and opening the PR. Pull requests opened with the default `GITHUB_TOKEN` do not trigger CI |

Run the responder by hand: **Actions → On-call responder → Run workflow** (from `main`), paste the
content of an `incidents/<trace_id>.json` file, and keep the target `demo/oncall-incident`.

**Demo only.** For the course demonstration the responder works exclusively on `demo/*` branches:
it checks the demo branch out, pushes the fix to a new `oncall/*` branch and opens the pull request
into the demo branch. A run for `main` or any other branch is refused, so it never changes the
main code. Removing the feature means deleting `.github/workflows/oncall-responder.yml`.

## 4. Runbook: a reading submission fails

1. The alert fires with a `trace_id`; the agent opens a fix pull request (or writes the incident
   file).
2. Look at the trace and the logs of that `trace_id` in Grafana (section 1) to confirm the cause.
3. Review the agent's pull request like any other change: the regression test must fail without
   the fix, CI must be green. Merge, and dev deploys automatically.
4. If users are blocked before a fix is ready, roll back (docs/DEPLOYMENT.md, "Rollback").

## 5. Demo: from a bug to a fix pull request

The branch `demo/oncall-incident` contains a deliberate bug (a "hotfix" that breaks updating an
existing reading). To replay it:

```bash
git switch demo/oncall-incident
docker compose up --build
# act as "Owner Apt 1" (it already submitted October) and update a reading → HTTP 500
```

Within about 15 seconds the alert fires, the agent writes the incident (or dispatches it), and the
responder opens a pull request into `demo/oncall-incident` with a regression test and the fix.
The result of the recorded run is linked in the Task 8 pull request.

## Known limits

- `LoggingHandler` of `opentelemetry-sdk` is deprecated in favour of
  `opentelemetry-instrumentation-logging`; switch when that package is added.
- The stack keeps its data in the container (lost on `docker compose down`), which is fine for
  local diagnosis. Production monitoring for the VPS is not part of the MVP.
