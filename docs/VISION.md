# Product Vision: Open-Source Apartment Association Ecosystem

## 1. Executive Summary
An open-source, lightweight, self-hosted platform for apartment associations, condominiums, and HOAs (e.g., Korteriühistu in Estonia, Taloyhtiö in Finland, WEG in Germany, OSMD/TSZh).

The platform replaces archaic, overpriced proprietary tools (like Korto.ee) and chaotic messenger chats with a unified ecosystem for utility meter tracking, emergency alerts, community discussions, and legally compliant governance.

## 2. Core Principles
1. **Single-Tenant / Self-Hosted by Default:**
   - One instance (Docker container + PostgreSQL DB) = One Apartment Association.
   - Zero risk of cross-building data leaks, trivial single-file database backups, and ultra-low hardware footprint (runs on a $4/mo VPS with 1 vCPU / 1 GB RAM).
   - Automated database migrations (`alembic upgrade head`) on container startup for non-technical administrators.
2. **Country-Agnostic Core with Country Plugins:**
   - The core domain handles universal concepts: Buildings, Entrances, Apartments, Residents, Meters, Readings, Announcements, Forum, and Tariffs.
   - Country-specific logic (Estonian SEPA e-arved, local accounting exports, national eID integrations) lives strictly inside `src/plugins/countries/{iso_code}/`.
3. **Privacy by Design (GDPR & DevTools Safe):**
   - Residents control how their identity appears to neighbors on the forum (`APARTMENT_ONLY` [default], `INITIALS`, or `FULL_NAME`), paired with a role badge (`OWNER`, `RESIDENT`, `BOARD`).
   - Masking happens strictly on the backend: API responses never expose raw names, emails, phones, or national ID codes (`isikukood`) to other residents.
4. **Cryptographic Legal Readiness (Future Governance):**
   - Authentication is separated from identity providers (`user_identities` stores salted SHA-256 hashes of national IDs for zero-PII-leak eID/Smart-ID login).
   - Formal voting supports zero-cost `.asice` (BDOC) container verification via SHA-256 ballot hash matching, ensuring 100% court and bank admissibility without requiring paid Smart-ID API contracts for self-hosted instances.

## 3. Two-Tier Role Model (RBAC)
### Building-Level Roles:
- **Chairman (`CHAIRMAN`):** Full system admin, building setup, meter management, period locking/reopening, manual reading overrides, emergency broadcasts.
- **Board Member (`BOARD`):** View building stats, publish official notices & emergency alerts, moderate forum.
- **Accountant (`ACCOUNTANT`):** Manage reading periods, inspect anomalies, trigger average estimations, export billing data.
- **Regular User (`USER`):** Accesses features based on their Apartment Membership permissions.

### Apartment-Level Hierarchy (`ApartmentMembership`):
- **Owner (`OWNER` — Apartment Admin):** Can manage residents/tenants in their apartment, configure their permissions, submit readings, view bills, and holds exclusive voting rights (`can_vote = True`).
- **Tenant / Family Member (`TENANT` / `RESIDENT`):** Granular flags controlled by the Owner (`can_submit_readings: bool`, `can_view_bills: bool`, `can_vote: False`).