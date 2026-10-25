# REST API Specification (FastAPI `/api/v1`)

All responses include `X-Trace-Id` header populated by OpenTelemetry middleware for observability correlation.

## 0. Authentication & Error Format

Every `/api/v1` endpoint requires an authenticated user:
- `Authorization: Bearer <jwt>`: HS256 token with `sub = <user uuid>` and `exp`. It is accepted only when `JWT_SECRET_KEY` is configured.
- `X-Demo-User-Id: <user uuid>`: accepted only when `DEMO_MODE_ENABLED=true` (local Docker, tests and the public demo). If both headers are sent, the Bearer token wins.

All errors share one JSON shape, `{"error_code": "...", "message": "...", ...extra}`:

| Status | `error_code` | When |
| :--- | :--- | :--- |
| 401 | `NOT_AUTHENTICATED` / `INVALID_TOKEN` / `UNKNOWN_USER` | No credentials, a bad or expired token, or an unknown or inactive user |
| 403 | `FORBIDDEN` | Authenticated, but the building role is not allowed |
| 404 | `NOT_FOUND` / `MEMBERSHIP_NOT_FOUND` | Unknown route, or a resource that does not exist or is not yours |
| 422 | `VALIDATION_ERROR` | Request body or parameters are invalid; details are in `errors` |
| 500 | `INTERNAL_SERVER_ERROR` | Unhandled error; the body carries `trace_id` |

### Author badge rules (`property.policies.format_author_badge`)
| `privacy_display_mode` | With apartment | Without apartment (staff) |
| :--- | :--- | :--- |
| `APARTMENT_ONLY` (default) | `Кв. 14` | Role label: `Председатель` / `Правление` / `Бухгалтер` / `Житель` |
| `INITIALS` | `И. П. (Кв. 14)` | `И. П.` |
| `FULL_NAME` | `Иван Петров (Кв. 14)` | `Иван Петров` |

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
The top-level `privacy_display_mode` is the mode of the first membership (ordered by entrance, then apartment number), or `APARTMENT_ONLY` when the user has none.

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
POST /api/v1/meters/readings
Submits or updates a meter reading for an open period.
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
POST /api/v1/meters/periods/{period_code}/estimate-missing
(Requires role: CHAIRMAN or ACCOUNTANT)
Finds all active apartment meters without a reading in period_code, calculates their 3-month rolling average delta, inserts readings with source = "AUTO_ESTIMATED", and writes audit logs.
PATCH /api/v1/meters/periods/{period_code}/status
Transitions period status (OPEN_FOR_RESIDENTS 
↔
↔
 REVIEW_BY_BOARD 
↔
↔
 LOCKED).
GET /api/v1/meters/readings/{reading_id}/history
Returns chronological audit trail from meters_reading_logs.
GET /api/v1/meters/periods/{period_code}/export-csv
Executes plugins/common/csv_export and streams a CSV file of the period's readings.
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