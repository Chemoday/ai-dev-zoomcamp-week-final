# REST API Specification (FastAPI `/api/v1`)

All responses include `X-Trace-Id` header populated by OpenTelemetry middleware for observability correlation.

## 0. Authentication & Error Format

Every `/api/v1` endpoint requires an authenticated user:
- `Authorization: Bearer <jwt>`: HS256 token with `sub = <user uuid>` and `exp`. It is accepted only when `JWT_SECRET_KEY` is configured.
- `X-Demo-User-Id: <user uuid>`: accepted only when `DEMO_MODE_ENABLED=true` (local Docker, tests and the public demo). If both headers are sent, the Bearer token wins.

All errors share one JSON shape, `{"error_code": "...", "message": "...", ...extra}`:

| Status | `error_code` | When |
| :--- | :--- | :--- |
| 401 | `NOT_AUTHENTICATED` / `INVALID_TOKEN` / `UNKNOWN_USER` | No credentials, a bad or expired token, or an unknown or inactive user (sent with `WWW-Authenticate: Bearer`) |
| 403 | `FORBIDDEN` | Authenticated, but the building role is not allowed |
| 404 | `NOT_FOUND` / `MEMBERSHIP_NOT_FOUND` | Unknown route, or a resource that does not exist or is not yours |
| 405 | `METHOD_NOT_ALLOWED` | The route exists but not for this HTTP method |
| 422 | `VALIDATION_ERROR` | Request body or parameters are invalid; details are in `errors` |
| 500 | `INTERNAL_SERVER_ERROR` | Unhandled error; the body carries `trace_id` |
| other | `HTTP_<status>` | Any other framework-level HTTP error |

### Author badge rules (`property.policies.format_author_badge`)
| `privacy_display_mode` | With apartment | Without apartment context |
| :--- | :--- | :--- |
| `APARTMENT_ONLY` (default) | `Кв. 14` | Role label: `Председатель` / `Правление` / `Бухгалтер` / `Житель` |
| `INITIALS` | `И. П. (Кв. 14)` | `И. П.` |
| `FULL_NAME` | `Иван Петров (Кв. 14)` | `Иван Петров` |

- **With an apartment**, the mode is that membership's mode. If the author is no longer a member of that apartment, `APARTMENT_ONLY` is used, because nobody consented to show the name there.
- **Without an apartment context** (e.g. a board notice), the mode is the *most private* of the author's memberships, so consenting to be named in one apartment never reveals the name anywhere else. A user with no membership (e.g. an external accountant) always shows the role label.

`role_badge`: `BOARD` for `CHAIRMAN` / `BOARD` building roles (this takes precedence, so neighbours know they are talking to the board), `ACCOUNTANT` for accountants, otherwise `OWNER` for owners and `RESIDENT` for tenants and residents.

---

## 1. Profile & Privacy Settings (`property`)

### `GET /api/v1/property/me`
Returns the current user's role, apartments, permissions, and active privacy display mode.
The payload never contains the raw `full_name`, `email` or `phone`, not even the user's own. Names appear only inside a `preview_badge`, and only to the degree the chosen display mode reveals them.

**Response 200 OK:**
```json
{
  "user_id": "uuid",
  "building_id": "uuid",
  "building_role": "USER",
  "privacy_display_mode": "APARTMENT_ONLY",
  "memberships": [
    {
      "membership_id": "uuid",
      "apartment_id": "uuid",
      "apartment_number": "14",
      "entrance": 2,
      "floor": 4,
      "relation_type": "OWNER",
      "can_submit_readings": true,
      "can_view_bills": true,
      "can_vote": true,
      "privacy_display_mode": "APARTMENT_ONLY",
      "preview_badge": { "display_label": "Кв. 14", "role_badge": "OWNER" }
    }
  ]
}
```
Memberships are ordered by entrance, then apartment number in natural order (`2`, `10`, `14`, `14A`). The top-level `privacy_display_mode` is the most private mode across the memberships (`APARTMENT_ONLY` when there are none); it is the mode used when the user acts without an apartment context. Only memberships of apartments in the user's own building are returned.

### `PATCH /api/v1/property/memberships/{membership_id}/privacy`
Updates how the user is displayed to neighbors in the micro-forum.
Only the user of that membership may change it. A membership that belongs to someone else returns `404 MEMBERSHIP_NOT_FOUND`, the same as an unknown one, so membership IDs cannot be probed.
**Request:**
```json
{
  "privacy_display_mode": "INITIALS" // "APARTMENT_ONLY" | "INITIALS" | "FULL_NAME"
}
Response 200 OK:
code
JSON
{
  "membership_id": "uuid",
  "privacy_display_mode": "INITIALS",
  "preview_badge": {
    "display_label": "И. П. (Кв. 14)",
    "role_badge": "OWNER"
  }
}
2. Meter Readings & Two-Step Anomaly Flow (meters)
GET /api/v1/meters/my-dashboard?period_code=2026-10
Returns the resident's apartment meters, previous readings, current period status, and deadline.
`period_code` is optional and defaults to the building's latest period. `period` is `null` when the building has no periods yet. Only active meters of the user's own apartments are listed.
```json
{
  "period": { "period_code": "2026-10", "status": "OPEN_FOR_RESIDENTS", "due_date": "2026-11-04T23:59:59Z" },
  "apartments": [
    {
      "apartment_id": "uuid", "apartment_number": "14", "can_submit_readings": true,
      "meters": [
        {
          "meter_id": "uuid", "resource_type": "COLD_WATER", "serial_number": "SN-1", "location_label": "Bathroom",
          "unit": "m3", "previous_value": "124.000",
          "reading": { "reading_id": "uuid", "value": "128.200", "consumption_delta": "4.200", "source": "RESIDENT", "is_anomaly_confirmed": false }
        }
      ]
    }
  ]
}
```
`reading` is `null` until something was submitted for that period.

POST /api/v1/meters/readings
Submits or updates a meter reading for an open period.
Who may submit, and in which period status:

| Caller | Meter | Allowed period status | Stored `source` |
| :--- | :--- | :--- | :--- |
| Member of the meter's apartment with `can_submit_readings` | apartment meter | `OPEN_FOR_RESIDENTS` (otherwise `409 PERIOD_CLOSED`) | `RESIDENT` |
| `CHAIRMAN` / `ACCOUNTANT` | any meter of the building, including master meters | `OPEN_FOR_RESIDENTS`, `REVIEW_BY_BOARD` (otherwise `409 PERIOD_LOCKED`) | `ADMIN_OVERRIDE` |

- Changing a reading that already exists as `ADMIN_OVERRIDE` requires a `reason` (`422 REASON_REQUIRED`).
- An unknown meter, or one the caller may not see, returns `404 METER_NOT_FOUND`. A member without `can_submit_readings` gets `403 FORBIDDEN`. An unknown period returns `404 PERIOD_NOT_FOUND`.
- Re-submitting the identical value is a no-op: no audit row, no event.
- `previous_value` is the meter's reading in the latest earlier period, or `initial_value` if there is none.
- `average_delta` follows docs/ROADMAP_MVP.md, section C.
- Submissions for one meter are serialized with a row lock on the meter.
Request:
code
JSON
{
  "meter_id": "uuid",
  "period_code": "2026-10",
  "value": "1254.000",
  "confirmation_level": 0,
  "reason": null
}
Response Scenarios:
422 Unprocessable Entity (Value lower than previous reading):
code
JSON
{
  "error_code": "READING_BELOW_PREVIOUS",
  "previous_value": "124.000",
  "submitted_value": "120.000",
  "message": "New reading cannot be lower than previous reading (124.000)."
}
409 Conflict — Popup #1 (confirmation_level == 0 and delta > threshold):
code
JSON
{
  "error_code": "ANOMALY_CONFIRMATION_STEP_1",
  "required_confirmation_level": 1,
  "consumption_delta": "1130.000",
  "average_delta": "4.200",
  "max_reasonable_delta": "25.000",
  "message": "Вы ввели слишком большое число (расход 1130.000 м³). Вы точно уверены, что всё нормально?"
}
409 Conflict — Popup #2 (confirmation_level == 1 and delta > threshold):
code
JSON
{
  "error_code": "ANOMALY_CONFIRMATION_STEP_2",
  "required_confirmation_level": 2,
  "consumption_delta": "1130.000",
  "message": "Проверьте, что данные введены точно! Они пойдут в ваш счет, это очень большой расход."
}
200 OK (Normal delta OR confirmation_level >= 2):
Saves reading, appends row to meters_reading_logs, emits MeterReadingSubmitted event.
code
JSON
{
  "reading_id": "uuid",
  "meter_id": "uuid",
  "period_code": "2026-10",
  "value": "1254.000",
  "consumption_delta": "1130.000",
  "source": "RESIDENT",
  "is_anomaly_confirmed": true
}
3. Chairman / Accountant Matrix & Controls (meters)
GET /api/v1/meters/periods/{period_code}/matrix
(Requires role: CHAIRMAN, BOARD, or ACCOUNTANT)
Returns:
Building Master Meters summary (master_total_m3 vs apartments_sum_m3 and unallocated_loss_m3).
List of all apartments with submission status (SUBMITTED, MISSING, AUTO_ESTIMATED, ANOMALY_CONFIRMED).
```json
{
  "period": { "period_code": "2026-10", "status": "REVIEW_BY_BOARD", "due_date": "2026-11-04T23:59:59Z" },
  "balances": [
    { "resource_type": "COLD_WATER", "unit": "m3", "master_total": "52.000", "apartments_sum": "48.500", "unallocated_loss": "3.500" }
  ],
  "apartments": [
    {
      "apartment_id": "uuid", "apartment_number": "14", "entrance": 2, "floor": 4, "status": "ANOMALY_CONFIRMED",
      "meters": [
        { "meter_id": "uuid", "resource_type": "COLD_WATER", "location_label": "Bathroom", "unit": "m3", "status": "ANOMALY_CONFIRMED",
          "reading": { "reading_id": "uuid", "value": "1254.000", "consumption_delta": "1130.000", "source": "RESIDENT", "is_anomaly_confirmed": true } }
      ]
    }
  ]
}
```
- `balances` has one row per resource type that has master meters, so the generic names also cover kWh and MWh: `master_total_m3` is `master_total` with `unit` `m3`.
- `master_total` is `null` while any master meter of that type has no reading; then `unallocated_loss` is `null` too.
- `unallocated_loss = master_total - apartments_sum` can be negative, which points to a wrong reading.
- A meter's status is `MISSING` without a reading; otherwise `ANOMALY_CONFIRMED`, `AUTO_ESTIMATED` or `SUBMITTED`.
- An apartment's status is the most urgent of its meters' statuses, in the order `MISSING` > `ANOMALY_CONFIRMED` > `AUTO_ESTIMATED` > `SUBMITTED`.
- Apartments without active meters are not listed. The payload contains no names.
POST /api/v1/meters/periods/{period_code}/estimate-missing
(Requires role: CHAIRMAN or ACCOUNTANT)
Finds all active apartment meters without a reading in period_code, calculates their 3-month rolling average delta, inserts readings with source = "AUTO_ESTIMATED", and writes audit logs.
- Allowed only in `REVIEW_BY_BOARD` (`409 PERIOD_NOT_IN_REVIEW`), so residents can no longer submit.
- The estimated `value` is `previous_value + average`, where the average defaults to `0.000` without history. The reading has `submitted_by_user_id = NULL`; its audit row records the triggering user and the reason `"3-month avg estimate"`.
- Running it again estimates nothing new.
- Response: `{"period_code": "2026-10", "estimated_count": 2, "estimated": [{"reading_id", "meter_id", "apartment_id", "value", "consumption_delta"}]}`.
PATCH /api/v1/meters/periods/{period_code}/status
Transitions period status (OPEN_FOR_RESIDENTS ↔ REVIEW_BY_BOARD ↔ LOCKED).
- Requires role `CHAIRMAN` or `ACCOUNTANT`.
- Request: `{"status": "REVIEW_BY_BOARD"}`. Response: `{"period_code": "2026-10", "previous_status": "OPEN_FOR_RESIDENTS", "status": "REVIEW_BY_BOARD"}`.
- Allowed transitions:
  - `OPEN_FOR_RESIDENTS → REVIEW_BY_BOARD`
  - `REVIEW_BY_BOARD → OPEN_FOR_RESIDENTS | LOCKED`
  - `LOCKED → REVIEW_BY_BOARD` (reopen before invoices are sent)
- Anything else, including skipping a step or "changing" to the same status, returns `409 INVALID_PERIOD_TRANSITION` with `allowed_statuses`.
- A transition emits `ReadingPeriodStatusChanged`.
GET /api/v1/meters/readings/{reading_id}/history
Returns chronological audit trail from meters_reading_logs.
- Visible to `CHAIRMAN`, `BOARD` and `ACCOUNTANT`, and to members of the meter's apartment. Anyone else gets `404 READING_NOT_FOUND`.
- The actor is shown only as a masked author badge (`null` for the system), never as a name or user id.
- Response: `{"reading_id", "meter_id", "period_code", "entries": [{"changed_at", "old_value", "new_value", "source", "reason", "changed_by": {"display_label", "role_badge"} | null}]}`.

GET /api/v1/meters/periods/{period_code}/export-csv
Executes plugins/common/csv_export and streams a CSV file of the period's readings.
- Requires role `CHAIRMAN`, `BOARD` or `ACCOUNTANT`. Served as `text/csv; charset=utf-8` with `Content-Disposition: attachment; filename="readings-2026-10.csv"`.
- Columns: `period_code, entrance, apartment_number, meter_id, resource_type, serial_number, location_label, unit, previous_value, value, consumption_delta, source, is_anomaly_confirmed, recorded_at`.
- Master meters have an empty apartment, and `MASTER` in `apartment_number`.
- Text cells that would start a spreadsheet formula (`= + - @`, tab, CR) are prefixed with `'` (CSV-injection guard). No names.
4. Emergency Alerts & Community Forum (communication)
GET /api/v1/communication/emergencies/active
Returns active EMERGENCY posts matching the current user's building, entrance, or apartment ID.
GET /api/v1/communication/posts?post_type=FORUM_TOPIC
Returns feed of posts. Author details are strictly masked by backend privacy policy:
code
JSON
[
  {
    "id": "uuid",
    "post_type": "FORUM_TOPIC",
    "category": "GENERAL",
    "title": "Давайте установим велопарковку во дворе",
    "body": "Соседи, что думаете?",
    "author_badge": {
      "display_label": "Кв. 14",
      "role_badge": "OWNER"
    },
    "comments_count": 3,
    "created_at": "2026-10-25T10:00:00Z"
  }
]
POST /api/v1/communication/posts