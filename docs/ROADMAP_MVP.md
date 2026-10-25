3. Core Domain Rules (MVP)
A. Meter Scope & Replacement Lifecycle (meters)
Every meter has building_id (NOT NULL) and apartment_id (NULLABLE).
apartment_id = UUID -> Apartment sub-meter (submitted by resident).
apartment_id = NULL -> Building Master Meter in the basement (submitted by Chairman/Accountant to track total building consumption vs. apartment sum).
Meters have initial_value: Numeric(12, 3) and is_active: bool. Replacing a physical meter archives the old record and creates a new Meter with its own initial_value.
Floating-point types (float) are strictly forbidden. Use Decimal / NUMERIC(12, 3).
B. Reading Periods, 4th-Day Window & Audit Trail (meters)
Readings belong to a ReadingPeriod (e.g., period_code = "2026-10"), separate from recorded_at timestamp.
Period State Machine:
OPEN_FOR_RESIDENTS (Open until the 4th of the following month; residents can submit or update their reading).
REVIEW_BY_BOARD (Closed for residents; system generates AUTO_ESTIMATED readings based on 3-month average for missing apartments; Chairman/Accountant can edit/override).
LOCKED (Finalized; can be explicitly reopened back to REVIEW_BY_BOARD by Chairman/Accountant before invoices are sent).
One Active Row per Period: UNIQUE(meter_id, period_id) in meter_readings.
Full Audit Log: Every insert or update to a reading writes an immutable record to meter_reading_logs (old_value, new_value, source, changed_by_user_id, reason, changed_at).
C. Two-Step Anomaly Confirmation (meters)
When delta = new_value - previous_value exceeds max_reasonable_delta (or 3x historical average):
- Precisely: the delta is anomalous when `delta > max_monthly_delta` of the meter, OR when a historical average exists (`average_delta > 0`) and `delta > 3 × average_delta`. "Exceeds" is strict, so a delta equal to the limit is accepted. A value below `previous_value` is rejected outright (`ERR_BELOW_PREV` → `422 READING_BELOW_PREVIOUS`) at any confirmation level. Implemented by the pure function `meters.policies.validate_reading_delta`.
If request confirmation_level == 0: Return HTTP 409 Conflict (code: ANOMALY_CONFIRMATION_STEP_1 — triggers Popup #1: "Value is unusually high, are you sure?").
If request confirmation_level == 1: Return HTTP 409 Conflict (code: ANOMALY_CONFIRMATION_STEP_2 — triggers Popup #2: "Please double-check digits; this goes directly to billing!").
If request confirmation_level >= 2: Save reading with is_anomaly_confirmed = True.
The 3-month average (`calculate_rolling_average_delta`):
- **Window:** the meter's last 3 *qualifying* readings (see "What counts as history"), chronological. Estimates and confirmed anomalies are skipped, so the window can reach further back than 3 calendar months. Negative deltas inside the window (correction artefacts) are dropped, and the rest are averaged.
- **What counts as history:** only `RESIDENT`, `ADMIN_OVERRIDE` and `IOT_WEBHOOK` readings with `is_anomaly_confirmed = FALSE`. Estimates of estimates would drift, and one confirmed leak month (e.g. 1130 m³) would otherwise switch off the 3× rule for three months.
- **Default without history:** the caller passes it explicitly:
  - `0` for anomaly detection, so only the meter limit applies, and the 409 payload shows `average_delta: "0.000"`;
  - `0` for estimating a missing reading, so a meter without history is estimated at its previous value and no consumption is invented.
- **Rounding:** the result is rounded half-up to 0.001 *before* the 3× comparison. The 409 payload's `average_delta` is that same rounded value.
- **Values:** every meter value and delta is canonical NUMERIC(12, 3): exactly 3 decimals, never `-0`, and ASCII digits only on input.
D. Three-Tier Communication & Privacy Masking (communication)
Post types:
EMERGENCY: Sticky red banner in UI + immediate EventBus trigger. Supports geographical targeting (ALL_BUILDING, ENTRANCE, or APARTMENT_LIST using apartment entrance and floor).
OFFICIAL_NOTICE: Board announcements with optional comments.
FORUM_TOPIC: Resident micro-forum categorized by topics.
Author attribution is resolved dynamically via PropertyService.get_masked_author_badge(user_id, apartment_id) respecting the user's privacy_display_mode.
Targeting: residents see a targeted post only if one of their apartments is in the target (its entrance for ENTRANCE, the apartment itself for APARTMENT_LIST); the chairman and the board see all posts of the building. "Acknowledge" hides the banner for that user only; the board resolves an emergency to end it for everyone. Full rules: docs/API.md, section 4.




# Project Roadmap & Phase 1 MVP Specification

## Phase 1: Ecosystem Foundation & Zoomcamp MVP (CURRENT SCOPE)
Goal: Deliver a production-grade, extensible Modular Monolith implementing `property`, `meters`, and `communication`, fully instrumented with OpenTelemetry and an automated On-Call Agent.

### Functional Deliverables:
1. **Authentication & Property Setup (`core` + `property`):**
   - Role-based access (Chairman, Accountant, Owner, Tenant).
   - Building structure with Entrances, Floors, and Apartments.
   - Apartment Membership management & Privacy Display Mode selector (`APARTMENT_ONLY`, `INITIALS`, `FULL_NAME`).
   - Database schema includes `user_identities` (with `subject_hash`) ready for future Smart-ID/eID plugins.
2. **Water & Utility Meters Workflow (`meters`):**
   - Support for Apartment Meters and Building Master Meters (`apartment_id = NULL`).
   - Resident submission UI with 2-step anomaly confirmation popups (`confirmation_level: 0 -> 1 -> 2`).
   - Chairman/Accountant Matrix Dashboard: View all apartments for the current `ReadingPeriod`, spot missing readings or confirmed anomalies, trigger "Estimate by Average" for non-submitters, and manually override readings with audit reason.
   - Reading change history inspector (`meter_reading_logs`).
   - CSV export via `plugins/common/csv_export`.
3. **Emergency Alerts & Community Micro-Forum (`communication`):**
   - Active Emergency Banner at the top of the dashboard (targeted by Whole Building or specific Entrance) with "Acknowledge" button.
   - Official Notice Board and Resident Micro-Forum (posts + comments) with backend-enforced author privacy masking.
4. **DevOps, Observability & On-Call Agent (Course Criteria):**
   - `docker compose up` spins up Backend, Frontend, PostgreSQL, and OpenTelemetry Collector / observability stack.
   - Structured JSON logs, metrics, and traces correlated by `trace_id` (zero PII in logs).
   - User-impact alert rule (triggers on 5xx errors or unhandled exceptions in `/api/v1/meters/readings`).
   - On-Call Agent workflow: Receives alert context, diagnoses the issue via trace/logs, runs `pytest`, and opens a fix PR.
   - Deployment to a self-hosted VPS behind Caddy (HTTPS via `sslip.io` hostnames): CD auto-deploys `main` to `dev`, `prod` is promoted manually, and a failed health check rolls back to the previous image.

---

## Phase 2: Billing, Cost Allocation & Notifications (NEXT)
- Parent/Child expense distribution engine (allocating `Master Meter - Sum(Apartment Meters)` water losses).
- Telegram Bot & WebPush plugins subscribed to `EmergencyAlertPublished` and `ReadingDeadlineApproaching` events.
- Country plugin `ee/e_arve_xml` and accounting exports.

## Phase 3: Legal Governance, IoT & Managed Cloud
- Cryptographic Voting (`governance`): Generates PDF ballots per choice (`POOLT` / `VASTU`), stores SHA-256 digests, and verifies uploaded `.asice` containers automatically.
- Smart-ID / Web eID authentication plugin.
- AI OCR meter reading from smartphone photos.
- Automated Managed Cloud provisioning plane (Caddy + Docker API).