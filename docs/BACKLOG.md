# Product Backlog (after the Phase 1 MVP)

Known problems and planned extensions that are deliberately **not** part of the MVP. Each entry
describes the problem, what the current model already supports, what blocks it, and a suggested
direction. Phase 1 tasks live in [TASKS.md](TASKS.md); phases in [ROADMAP_MVP.md](ROADMAP_MVP.md).

---

## B-1. Several buildings and management companies

- **Status:** open, design only. Target: Phase 3 (together with the managed cloud plane).
- **Raised:** 2026-10-25, during Task 5.

### Problem
The MVP assumes one association = one building = one instance ([VISION.md](VISION.md),
"Single-Tenant / Self-Hosted by Default"). Real associations do not always look like that. Two
different cases exist:

1. **One association, several buildings.** An Estonian *korteriühistu* is formed per immovable
   property, and one property can hold several buildings. Many HOAs / TSZh / OSBB also cover a
   small group of houses. This is still **one client** with several buildings inside.
2. **One management company, many associations.** Professional managers serve dozens of
   independent associations: property management firms and outsourced accountants in Estonia,
   *isännöitsijä* offices in Finland, *WEG-Verwalter* in Germany, management companies (UK) in
   Russia and Ukraine. One person works for **many separate clients**.

### What already fits
- Every domain table carries `building_id`, and composite foreign keys keep each row chain inside
  one building (`meters_*`, `comm_posts` / `comm_comments`, author apartments). Data of two
  buildings cannot be mixed even when they share a database.
- Apartment memberships are per apartment, so a person can already be a member of apartments in
  different buildings at the table level.

### What blocks it
- `core_users.building_id` is `NOT NULL` and single: a user belongs to exactly one building, and
  `CurrentUserDTO.building_id` is the scope of every query.
- `core_users.building_role` is one role per user, so "chairman of building A, plain owner in
  building B" or "accountant of five buildings" cannot be expressed.
- `PropertyService._memberships_of` returns only memberships of the user's own building, and the
  forum, alerts and meters all scope by that one building.
- There is no entity above the building: no association that owns buildings, holds the board and
  could later hold billing and governance (votes are per association, not per building).

### Suggested direction (additive, expand/contract migrations)
1. **Association level.** Add `property_associations`; `property_buildings.association_id`.
   For case 1, board roles, official notices, votes and billing belong to the association;
   meters, entrances and emergencies stay per building.
2. **Roles per scope.** Replace `core_users.building_role` with a table
   `property_role_assignments (user_id, association_id, building_id NULL, role)`. Keep the old
   column during the transition (expand), drop it later (contract).
3. **Active context per request.** The caller picks the building or association they act in
   (path segment or an `X-Building-Id` header, validated against their assignments and
   memberships). `CurrentUserDTO` is built for that context, so existing services keep scoping by
   one `building_id` and do not change.
4. **Case 2 stays a deployment question.** Keep one instance per association (privacy, backups,
   zero cross-client leaks). A management company gets either one account per instance or a
   federated portal on the managed cloud plane that lists its instances. Moving to a multi-tenant
   database is possible but contradicts the single-tenant principle and needs its own decision.

### Open questions
- Is case 1 frequent enough for the target users to do it before billing (Phase 2)?
- For case 2: separate logins per instance, or one identity (Smart-ID / Web eID) across instances?
- Do official notices and forum topics belong to the association or to each building?

---

## B-2. Forum moderation beyond the MVP

- **Status:** open. Target: Phase 2. **Tracked in:**
  [#18](https://github.com/Chemoday/ai-dev-zoomcamp-week-final/issues/18).
- **Raised:** 2026-10-25, during Task 5.

### Current state (MVP)
The chairman can soft-delete a forum topic (`DELETE /api/v1/communication/posts/{id}`,
[API.md](API.md), section 4). The topic and its comments disappear for everyone; the row keeps
`deleted_at` and `deleted_by_user_id`. Emergencies are resolved instead, and official notices
cannot be deleted.

### Missing
- Authors edit or delete their own posts and comments.
- The board hides or deletes single comments, not only whole topics.
- Residents report abusive posts or comments; the board works through a report queue.
- A moderation log: who hid or deleted what, when and why, without PII in logs or spans.
- Retention: soft-deleted content is purged after a fixed period (GDPR), keeping only the audit
  facts (who, when, which post id).
- Edit history, so an edited post cannot silently change what neighbours already answered to.

### Open questions
- Should `BOARD` members moderate too? [VISION.md](VISION.md) says the board moderates the
  forum; the MVP allows only the chairman.
- Should a deleted topic show a placeholder ("Removed by the board") instead of vanishing, so the
  conversation history stays understandable?
- How long is the retention period, and does it differ for reported content?
