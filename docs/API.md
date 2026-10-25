# REST API Specification (FastAPI `/api/v1`)

All responses include `X-Trace-Id` header populated by OpenTelemetry middleware for observability correlation.

---

## 1. Profile & Privacy Settings (`property`)

### `GET /api/v1/property/me`
Returns the current user's role, apartments, permissions, and active privacy display mode.

### `PATCH /api/v1/property/memberships/{membership_id}/privacy`
Updates how the user is displayed to neighbors in the micro-forum.
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