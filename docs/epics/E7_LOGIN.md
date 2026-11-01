# E7. Login

- **Phase:** 2. **Status:** design reviewed by the owner and by the CTO review (2026-11-01);
  contract (A1) next. **Backlog:** docs/BACKLOG.md B-7. **Epic issue:** created with the merge of
  this page.
- **Goal:** people sign in as themselves, and only people the association invited can sign in.
  Today every environment runs in demo mode (`X-Demo-User-Id`: anyone can act as the chairman),
  so the external notification channels of E1 cannot be used for real.

This page is the design. The binding contract comes with task A1 (docs/SCHEMA.md, docs/API.md,
docs/ARCHITECTURE.md); where they differ, the contract wins. Background: the planning session of
2026-11-01 (docs/cli_planning/2026-11-01-login-and-profiles.md).

## 1. Owner decisions (2026-11-01)

- **Two ways to sign in:** a one-time link sent by e-mail (magic link), and an optional password
  of at least 15 characters. Smart-ID and the ID card come later as country plugins through
  `core_user_identities`.
- **Accounts by invitation only, with no self-registration:**
  - The chairman invites **owners** (the e-mail and the apartment number) and **staff** (the
    e-mail and a role). The chairman does not invite tenants, and board members do not invite.
  - An owner invites **tenants** of their own apartment (the e-mail only): at most
    `MAX_TENANTS_PER_APARTMENT` (default 3) per apartment, pending invitations included.
  - Family members get no access of their own. `RESIDENT` memberships are not created by E7.
  - The invited person completes the registration themselves: name, optional phone, language, and
    how the name is shown on the forum.
  - A CSV import of apartments and owners comes later with E5.
- **"Replace tenant" closes the old access and invites a new person.** An account is never handed
  over to another person, because it carries personal things: the notification feed, linked chats
  and browsers, sessions, forum comments, preferences. The meter history belongs to the apartment
  and stays.
- **Sessions:** a week without a visit means a new sign-in.
- **Demo mode stays on everywhere for now** (local, dev, prod), and external notification channels
  stay off in it (E1 10.10). Login works in demo mode too. Login e-mails in demo mode go out only
  where `AUTH_MAIL_IN_DEMO=true`, which is set only on an environment behind a password gate: local
  (Mailpit) and dev once dev has its gate (it has none today, see 3.8). The public prod sends
  none.

## 2. Scope

In scope:
- Sessions: a short access token and a refresh cookie; sign out; sign out everywhere; a list of
  own sessions.
- Signing in with an e-mail link, and with an optional password (set, change, reset).
- Invitations by the chairman (owners, staff) and by owners (tenants); resend and revoke;
  completing the registration; replacing a tenant.
- Granting an existing person another apartment or a staff role (chairman), e.g. an owner of two
  flats, or a board member who is also an owner.
- Ending a membership and closing an account, with the clean-up in other modules.
- The first chairman of a new installation (an operator command).
- E-mail texts (en, ru, et) and sending through the e-mail plugin.
- The frontend handoff: screens in docs/FRONTEND_GUIDE.md; the owner builds the frontend.

Out of scope:
- Smart-ID, the ID card, two-factor authentication.
- Changing one's own e-mail address (later; the chairman can correct an e-mail of an owner or
  staff member until then).
- Leaving demo mode in any environment: a separate owner decision with the checklist in 3.8.
- Apartment profiles and threads between the board and an apartment (E8).

## 3. How it works

### 3.1 Module and data ownership
- A new module `src/modules/auth/` owns:
  - `core_user_identities` (moved from `property`: the passwords and, later, the eID subjects);
  - `auth_sessions`, `auth_refresh_tokens` and `auth_tokens` (links: sign-in, invitation,
    registration);
  - `auth_attempts`.
- `property` keeps users, apartments and memberships. `auth` changes them only through new
  `PropertyService` system methods: create a pending user, activate, add or end a membership, set
  a role, close. `property` never imports `auth`, so the dependency points one way only.
- **Routes:** `/api/v1/auth/...`, invitations included. The chairman's user administration
  (grant, role, close) also lives in `auth`'s router and calls `PropertyService`.
- **Closing an account** stores a durable domain event `UserAccountClosed(user_id)` (ids only, in
  the same transaction). Notifications subscribes to it and deletes the person's bindings,
  preferences and link codes, and its pending deliveries are skipped (`USER_INACTIVE`). This fits
  E1's rule that notifications only consumes.

### 3.2 E-mail addresses
- **Canonical form:** trimmed, NFKC-normalised, lower-cased. Users store it and are looked up by
  it.
- **Uniqueness:** a unique index on the canonical form (a migration lower-cases the existing rows
  and stops if two collide).
- **Limits** count the keyed hash of the canonical form of whatever was submitted, whether or not
  an account exists.

### 3.3 Sessions
- **Access token:** the existing JWT (`src/core/security.py`).
  - Its lifetime changes from 60 to 15 minutes (`JWT_ACCESS_TOKEN_TTL_MINUTES`).
  - New claims: `sid` (the session) and `auth_time` (when the person actually signed in).
  - The client keeps it in memory only, and gets one on page load with `POST /auth/refresh`.
  - `get_current_user` additionally rejects a token whose session was revoked (one indexed
    lookup).
- **Refresh token:** 32 random bytes in the cookie `__Secure-refresh`:
  - `HttpOnly`, `Secure`, `SameSite=Lax`, `Path=/api/v1/auth`, no `Domain` (host only, so dev and
    prod never share it);
  - valid 7 days and extended on every refresh; a session ends after 90 days at the latest.
- **Tables:** `auth_sessions` is one row per sign-in: user, `authenticated_at`, `auth_method`,
  `expires_at`, the 90-day cap, `revoked_at` and a neutral device label such as "browser on
  desktop". `auth_refresh_tokens` holds the keyed hash, `rotated_at` and `replaced_by`.
- **Rotation:** every refresh replaces the token.
  - Within a 60-second grace window, a refresh with the token just replaced returns a new access
    token for the same session and **no** `Set-Cookie`: tabs share one cookie jar, so the browser
    already holds the successor from the first answer (only hashes are stored, so the successor
    could not be sent again). Two tabs that refresh at once therefore stay signed in.
  - A rotated token used later means theft: the session is revoked.
  - The refresh row is locked during a refresh.
- **CSRF:** every request that uses or sets the cookie needs the header `X-Requested-With`:
  refresh, logout, link redeem, password login, registration. CORS never allows credentialed
  cross-origin requests.
- **Sign out** needs only the cookie and the header: it revokes the session and clears the cookie.
  **Sign out everywhere** revokes all of the user's sessions and also clears the current cookie.
- Closing an account, a password change or reset, and leaving demo mode revoke sessions (see
  those sections).
- **The demo header never authenticates the account endpoints:** password, sessions, logout and
  registration need a Bearer token or the refresh cookie.

### 3.4 E-mail link (magic link)
1. `POST /auth/magic-link {"email"}` always answers `202` with the same body, and does the work
   after the response, so the answer and its timing say nothing about whether an account exists.
2. Only for an **active** user with that e-mail, a one-time token is created: 32 random bytes,
   valid 15 minutes, stored as its keyed hash (`AUTH_HASH_SECRET`). The e-mail links to
   `PUBLIC_BASE_URL/login/link#<token>`. For a person whose invitation is still pending, the
   invitation is resent instead (like Resend: it cancels the old link, the 7 days start again, and
   it counts toward the 5-per-hour limit).
3. The frontend page reads the fragment, removes it at once (`history.replaceState`), and calls
   `POST /auth/magic-link/redeem {"token"}` only after the person clicks "Sign in". Link scanners
   of mail providers therefore cannot use it up. The page sends `Referrer-Policy: no-referrer`.
4. Limits:
   - 5 links per e-mail per hour, 20 per IP address per hour (over the limit: still `202`,
     nothing sent);
   - 20 redeem attempts per IP address per hour.
   - Each valid link works once; a newer link does not cancel an older one.

### 3.5 Password (optional)
- **Storage:** Argon2id (`argon2-cffi` with its recommended parameters) in `core_user_identities`:
  `provider = 'password'`, `subject_hash` = the keyed hash of the user id, `secret_data` = the
  encoded hash, rehashed on sign-in when the parameters change. At most 2 hashes run at once, so a
  burst of logins cannot use up the memory of a small server.
- **Rules:** 15 to 128 characters after NFKC normalisation, and not on a short list of common
  passwords. No composition rules. A long phrase of words is easy to remember.
- **Set or change** (`PUT /auth/password {new_password, current_password?}`): needs `auth_time`
  within the last 10 minutes, or the correct `current_password` in the body. A wrong
  `current_password` counts toward the same throttling as the password login, so a stolen access
  token cannot be used to guess the password. A change or reset revokes all the user's other sessions and sends a "your password
  was changed" e-mail.
- **Login:** `POST /auth/password/login {"email", "password"}`.
  - Any failure gets one generic `401 INVALID_CREDENTIALS`, with the same timing (a dummy hash
    for an unknown e-mail).
- **Throttling,** counted in `auth_attempts` by the hash of the submitted e-mail (whether it
  exists or not) and by the IP address:
  - after 5 failures in 15 minutes for an e-mail, or 30 for an IP address, a growing delay
    applies (`429` with `Retry-After`, the same for known and unknown e-mails);
  - there is no lockout, so nobody can lock another person out.
- **Forgotten password:** sign in with an e-mail link and set a new one.
- The mailbox is the recovery root for both ways in. The password is a convenience, not a second
  factor.

### 3.6 Invitations and registration
- **Invite** (`POST /auth/invitations`):
  - the chairman: `{email, apartment_number}` for an owner, or `{email, role}` for staff;
  - an owner: `{email}` for a tenant of an apartment they own; it may name the apartment if they
    own several.
- **Pending state:** the invitation creates a pending user: `is_active = false`, `invited_at`
  set, `full_name` = "Invited", with a membership whose relation is `OWNER` or `TENANT`. A pending
  person cannot sign in, receives no notifications and is not a recipient. The inviter's list
  shows them as "invited". Most user queries already filter `is_active`, but not all (e.g.
  `has_delivery_email`, membership lists). A1 lists every user and membership query with its
  filter, and adds tests.
- **The invitation e-mail** holds a one-time link valid 7 days (purpose `invitation`).
  - Redeeming it returns no session, but a short-lived registration token (purpose
    `registration`, 30 minutes).
  - `POST /auth/registration {token, full_name, phone?, locale, privacy_display_mode}` completes
    the registration, activates the account (`activated_at`) and returns the session. Closing
    the tab only means using the link again while it is valid.
- **An e-mail that already belongs to an account:**
  - The chairman gets `409 EMAIL_ALREADY_REGISTERED`; the chairman sees all accounts anyway. To
    give that person another apartment or a staff role, the chairman uses "grant" (2. Scope).
  - An owner always gets `202`, so an owner can never test whether an e-mail has an account. If
    the e-mail is taken, no membership is created, and the holder gets an e-mail that suggests
    asking the chairman.
- **Resend** creates a new link and cancels the old one. **Revoke** deletes a pending invitation
  together with its pending user and membership.
- **Limits:** at most 10 invitations or resends per inviter per hour (`auth_attempts`, kind
  `invitation`), and at most one "this e-mail already has an account" mail per address per day.
- **Expiry:** an invitation whose newest link expired unused (7 days after the last send) is
  removed by the clean-up, together with the pending user and membership. That frees the e-mail
  and the tenant slot.
- **Replace tenant** (`POST /auth/apartments/{id}/tenants/{user_id}/replace {email}`, by an owner
  of that apartment) runs in one transaction: it ends the old tenant's membership (3.7) and
  invites the new e-mail.

### 3.7 Ending a membership and closing an account
- **An owner** may only end a tenant's membership in their own apartment
  (`DELETE /auth/apartments/{id}/tenants/{user_id}`).
  - `property_memberships.ended_at` is set (a new column). Every membership query filters
    `ended_at IS NULL`: current user, recipients, reading submitters, badges. A1 lists them.
  - Forum badges of a former member use the existing "no longer a member" fallback (API section
    0).
- **The account is closed** when its last active membership ends and it has no staff role, or
  when the chairman closes it (`DELETE /auth/users/{id}`). Closing:
  - sets `is_active = false`;
  - revokes all sessions and deletes the user's tokens and `core_user_identities` rows;
  - stores `UserAccountClosed` (3.1);
  - removes personal data: e-mail and phone become `NULL`, the name becomes "Former resident"
    (GDPR). Readings keep the apartment, and forum posts keep their masked badge;
  - frees the e-mail, so the same person can be invited again later.
- **The chairman** can also end any single membership
  (`DELETE /auth/users/{id}/memberships/{membership_id}`), e.g. an owner who sold one of two
  flats, or tenants of an apartment whose last owner left. The same auto-close rule applies.
  Setting a staff member without a membership to `USER` closes the account, after a confirmation
  in the UI.
- **The last active chairman** cannot be closed or lose the role. A chairman can make another
  person `CHAIRMAN` (a handover) and then step down.
- There is no separate reversible "deactivation": closing is the only way to take access away.

### 3.8 Demo mode and the first chairman
- All login endpoints work in demo mode. `X-Demo-User-Id` keeps working only while demo mode is
  on, and never for the account endpoints (3.3).
- **Login e-mails in demo mode** are sent only with `AUTH_MAIL_IN_DEMO=true`. Otherwise the
  endpoints answer as usual and send nothing; without the e-mail plugin nothing is sent either way.
  - The app refuses to start with `AUTH_MAIL_IN_DEMO=true` and `ENVIRONMENT=prod`.
  - **A precondition of A3 for dev:** a password gate for `DEV_HOST` (Caddy `basic_auth`, a note
    in docs/DEPLOYMENT.md). Today `deploy/edge/Caddyfile` serves dev openly, so dev keeps
    `AUTH_MAIL_IN_DEMO=false` until the gate exists. Otherwise anyone acting as the chairman could
    make the server e-mail any address.
  - Optionally, `AUTH_DEMO_MAIL_DOMAINS` limits the recipients in demo mode to a list of domains,
    as a second guard.
- **Secrets:** `JWT_SECRET_KEY` and `AUTH_HASH_SECRET` (at least 32 bytes) are always required, in
  demo mode too; the app refuses to start without them. A2 adds local-only values to
  `docker-compose.yml` and `tests/conftest.py`, per-environment `.env` entries and a
  docs/DEPLOYMENT.md note, so `docker compose up --build` and `pytest` keep working. Rotating
  `AUTH_HASH_SECRET` signs everyone out and voids all open links and invitations.
- **First chairman:** `uv run python -m src.modules.auth create-chairman --email ...` (an operator
  command) creates a pending chairman and prints nothing secret; the invitation goes out by
  e-mail.
- **Leaving demo mode later** (a checklist for that decision):
  1. rotate `JWT_SECRET_KEY` and `AUTH_HASH_SECRET`, which revokes every session and link;
  2. delete all password identities (every one of them dates from the demo period);
  3. review the accounts and invitations created in demo mode (by `created_at`) and close the
     ones that are not real;
  4. configure a real SMTP sender and domain.

### 3.9 E-mails
- **Sender:** the `auth` module sends through the e-mail plugin (`registry.channel("email")`,
  `common.email_smtp`) directly, not through notifications: login must not depend on
  `NOTIFICATIONS_ENABLED`, and a token must never be stored in a delivery row.
- **When:** after the commit, outside the request (a background task). The plain token exists
  only in memory between creating it and sending it. If the send fails, the person asks for a new
  link, or the inviter resends.
- **Texts:** system texts in en, ru and et in `src/modules/auth/messages.csv`, with the same
  catalog format as E1. The loader moves to `src/core/messages.py`. The repository language guard
  (`tests/core/test_repository_language.py`) is extended to allow Cyrillic in both CSV files.
- These e-mails are transactional: they carry no unsubscribe link (E1/N8).

## 4. Data model (draft; final version in docs/SCHEMA.md, task A1)
- `core_users`: plus `invited_at`, `activated_at`; `email` stored canonical with a unique index.
- `property_memberships`: plus `ended_at`.
- `core_user_identities`: moves to `auth`. Its descriptions are updated: Argon2id, and a keyed
  HMAC for subjects.
- `auth_sessions`, `auth_refresh_tokens` (3.3).
- `auth_tokens`: `user_id`, `purpose` (`magic_link`, `invitation`, `registration`),
  `token_hash` (unique), `created_at`, `expires_at`, `used_at`, `invited_by_user_id`.
- `auth_attempts`: `kind` (`magic_link`, `redeem`, `password`, `invitation`), `subject_hash`
  (keyed hash of the e-mail, the IP address or the inviter's user id), `succeeded`,
  `attempted_at`. Rows older than a day are deleted.

## 5. API (draft; final version in docs/API.md, task A1)

| Method | Path | Who | Purpose |
|---|---|---|---|
| POST | `/api/v1/auth/magic-link` | anyone | send a sign-in link (always 202) |
| POST | `/api/v1/auth/magic-link/redeem` | a link holder | sign in, or start the registration of an invitation |
| POST | `/api/v1/auth/registration` | a registration token | complete the registration, get the session |
| POST | `/api/v1/auth/password/login` | anyone | sign in with a password |
| PUT | `/api/v1/auth/password` | signed in, recently | set or change the password |
| POST | `/api/v1/auth/refresh` | refresh cookie | new access token, rotated cookie |
| POST | `/api/v1/auth/logout` | refresh cookie | end this session |
| GET / DELETE | `/api/v1/auth/sessions[/{id}]` | signed in | own sessions; end one or all |
| POST / GET | `/api/v1/auth/invitations` | chairman (owners, staff), owner (tenants) | invite; list own open invitations |
| POST | `/api/v1/auth/invitations/{id}/resend` | the inviter, chairman | new link |
| DELETE | `/api/v1/auth/invitations/{id}` | the inviter, chairman | revoke |
| POST | `/api/v1/auth/apartments/{id}/tenants/{user_id}/replace` | owner of the apartment | replace a tenant |
| DELETE | `/api/v1/auth/apartments/{id}/tenants/{user_id}` | owner of the apartment | end a tenant's membership |
| POST | `/api/v1/auth/users/{id}/memberships` | chairman | grant another apartment to an existing person |
| PUT | `/api/v1/auth/users/{id}/role` | chairman | set the staff role (last-chairman rule) |
| DELETE | `/api/v1/auth/users/{id}/memberships/{membership_id}` | chairman | end one membership |
| DELETE | `/api/v1/auth/users/{id}` | chairman | close an account |

- Answers that could reveal whether an e-mail has an account are the same for both cases. The
  only exception is the chairman's `409 EMAIL_ALREADY_REGISTERED`.
- What the lists show: the chairman sees full e-mails of invitations; an owner sees the e-mails
  they invited. Who may see phone numbers is decided with E8.

## 6. Privacy and security
- **Never in logs or spans:** e-mails, tokens, passwords, cookies or IP addresses; only keyed
  hashes are stored. New scrubber markers: `refresh`, `magic`, `client_ip`, `ip_address` (it
  already has `token`, `email`, `password`, `secret`, `cookie`).
- **Tokens** are 32 random bytes from `secrets` and are found by hash lookup.
- uvicorn already runs without an access log. The reverse proxy keeps its access log off, or
  excludes `Cookie` and `Authorization` (a note in docs/DEPLOYMENT.md).

## 7. Tasks

Each task is one issue, one branch from `develop`, one PR into `develop`, and passes the CTO
review before merge (AGENTS.md section 4).

| # | Task | Done when |
|---|---|---|
| A1 | Contract: SCHEMA, API, ARCHITECTURE (module `auth`, ownership, the demo and e-mail rules), FRONTEND_GUIDE (sign-in, registration, invitations, sessions, my residents) | the owner approved the API |
| A2 | Module `auth`, sessions: tables, refresh with rotation, grace window and reuse detection, logout, sessions list, `sid`/`auth_time`, the 15-minute access token, the canonical e-mail migration | tests: rotation, two tabs at once, a reused token revokes, expiry and the 90-day cap, cookie flags, the CSRF header, the demo header rejected on account endpoints |
| A3 | Sign in with an e-mail link, and the auth e-mail sender; precondition for dev: the password gate (3.8) | the same answer and timing for known and unknown e-mails; one-time, 15 minutes; limits; demo-mode mail rule; tests with the test SMTP server |
| A4 | Invitations, registration, grant, roles, ending a membership, closing an account, replace tenant, the first-chairman command | tests: the pending user cannot sign in or receive notifications; registration activates; the chairman invites only owners and staff; owners only tenants, at most 3 with pending ones; the owner never learns whether an e-mail exists; closing wipes personal data and notifications drops the bindings; the last chairman stays |
| A5 | Optional password | Argon2id; 15 characters; one generic error and timing; throttling without lockout; a change revokes other sessions; a reset through the e-mail link |
