# E7. Login

- **Phase:** 2. **Status:** design reviewed by the owner and by the CTO review (2026-11-01);
  contract (A1, [#64](https://github.com/Chemoday/ai-dev-zoomcamp-week-final/issues/64)) approved
  and merged (#72); sessions (A2) and the e-mail link (A3) done; invitations (A4) next. **Backlog:** docs/BACKLOG.md B-7.
  **Epic issue:**
  [#69](https://github.com/Chemoday/ai-dev-zoomcamp-week-final/issues/69).
- **Goal:** people sign in as themselves, and only people the association invited can sign in.
  Today every environment runs in demo mode (`X-Demo-User-Id`: anyone can act as the chairman),
  so the external notification channels of E1 cannot be used for real.

This page is the design. The binding contract is task A1: docs/SCHEMA.md sections 1, 2 and 7,
docs/API.md sections 0 and 7, docs/ARCHITECTURE.md section 4 and docs/FRONTEND_GUIDE.md section
12. Where they differ, the contract wins. Background: the planning session of
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
- Giving an existing person another apartment or a staff role (an invitation they accept), e.g. an
  owner of two flats, or a board member who is also an owner.
- Ending a membership and closing an account, with the clean-up in other modules.
- The first chairman of a new installation (an operator command).
- E-mail texts (en, ru, et) and sending through the e-mail plugin.
- The frontend handoff: screens in docs/FRONTEND_GUIDE.md; the owner builds the frontend.

Out of scope:
- Smart-ID, the ID card, two-factor authentication.
- Changing an e-mail address: later, by the person, with a confirmation of both addresses. Nobody
  else may change it, since redirecting someone's e-mail would hand over their account.
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
  `PropertyService` system methods (ARCHITECTURE 4.2): create a user at registration, open or end
  a membership, set a role, close. `property` never imports `auth`, so the dependency points one way only.
- **Routes:** `/api/v1/auth/...`, invitations included. The chairman's user administration
  (role, memberships, close) also lives in `auth`'s router and calls `PropertyService`.
- **Closing an account** stores a durable domain event `UserAccountClosed(user_id, building_id)`
  (ids only, in the same transaction). Notifications subscribes to it and deletes the person's bindings,
  preferences and link codes, and its pending deliveries are skipped (`USER_INACTIVE`). This fits
  E1's rule that notifications only consumes. Its clean-up does the same for every inactive user,
  for the time notifications were switched off (ARCHITECTURE 4.1).

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
  `expires_at`, the 90-day cap, `revoked_at` and a coarse `device_kind` (`DESKTOP_BROWSER`,
  `MOBILE_BROWSER`, `UNKNOWN`), which the frontend localizes. `auth_refresh_tokens` holds the keyed hash, `rotated_at` and `replaced_by`.
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
   invitation is resent instead: a new link cancels the old one, and the expiry stays the same
   (only the inviter or the chairman can extend it).
3. The frontend page reads the fragment, removes it at once (`history.replaceState`), and calls
   `POST /auth/magic-link/redeem {"token"}` only after the person clicks "Sign in". Link scanners
   of mail providers therefore cannot use it up. The page sends `Referrer-Policy: no-referrer`.
4. Limits:
   - 5 links per e-mail and IP address together per hour, 20 per e-mail per day, 20 per IP
     address per hour (over a limit: still `202`, nothing sent). Counting the e-mail together
     with the address means somebody else cannot use up a person's links; the daily cap stops
     mail bombing;
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
- **Throttling,** counted in `auth_attempts` by hashes of the submitted e-mail (whether it exists
  or not) and the IP address:
  - after 5 failures in 15 minutes for the e-mail and IP address together, or 30 for an IP
    address, a growing delay applies (`429` with `Retry-After`, the same for known and unknown
    e-mails);
  - after 100 failures for an e-mail within a day, its password is refused until its owner has
    signed in with an e-mail link once;
  - there is no lockout of signing in. Accepted residual risk: the silent daily cap on links can
    block a person's new sign-ins for at most a day; a live session is unaffected.
- **Forgotten password:** sign in with an e-mail link and set a new one.
- The mailbox is the recovery root for both ways in. The password is a convenience, not a second
  factor.

### 3.6 Invitations and registration
*(Refined by the contract, A1: docs/API.md section 7 is binding.)*
- **Invite** (`POST /auth/invitations`):
  - the chairman: `{kind: OWNER, email, apartment_number}` or `{kind: STAFF, email, building_role}`;
  - an owner: `{kind: TENANT, email, apartment_id}` for an apartment they own.
- **No pending users.** An invitation is its own row (`auth_invitations`) holding the e-mail and
  the target. No account exists until it is accepted, so nobody half-registered can sign in,
  receive notifications or count as a resident. Pending tenant invitations count toward the
  tenant limit.
- **The invitation e-mail** holds a one-time link valid 7 days.
  - **A new person:** redeeming the link returns a registration token (30 minutes).
    `POST /auth/registration` creates the account with the membership or role and signs the
    person in. Closing the tab only means opening the link again while it is valid.
  - **An existing account** (an owner of a second flat, a board member who is also an owner):
    the link never creates a session (a 7-day link must not become a sign-in). The person signs
    in the usual way, sees what the invitation grants, and clicks "Accept"
    (`POST /auth/invitations/accept`).
  - **It never lowers anything:** an existing membership in the apartment stays as it is (an
    owner invited as a tenant stays an owner), and a staff invitation applies only to an account
    without a staff role (a `CHAIRMAN` invitation raises any role, for recovery).
  - **Answers:** the inviter gets the same answer for an e-mail with or without an account
    (`201`). There is no `409 EMAIL_ALREADY_REGISTERED` for inviters, no separate "grant"
    endpoint, and no "already registered" e-mail.
- **One open invitation per target,** from anyone (so two co-owners cannot both use up tenant
  slots for the same person). **Resend** works on pending and expired invitations: it creates a new
  link, cancels the old one, and starts the 7 days again. **Delete** removes an open invitation.
- **Limits:** at most 10 invitations or resends per inviter per hour.
- **Expiry:** an invitation whose newest link expired unused shows as `EXPIRED`. Accepted and
  expired invitations are deleted after 30 days.
- **An invitation stops applying** when a `TENANT` invitation's apartment has no active owner any
  more, an `OWNER` or `STAFF` invitation's building has no active chairman, the apartment is gone,
  or the account belongs to another building.
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
  - The app refuses to start with `AUTH_MAIL_IN_DEMO=true` unless `ENVIRONMENT` is `local`, `dev`
    or `test` (an allowlist, so an unknown name fails closed).
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
  command) creates a `CHAIRMAN` staff invitation and sends it.
  - `--force` also works when a chairman exists, as a recovery when the only chairman lost their
    mailbox.
  - `--print-link` prints the link to the operator's terminal when mail cannot go out.
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

## 4. Data model
Final version: docs/SCHEMA.md section 1 (`core_users`, `core_user_identities`), section 2
(`property_memberships.ended_at`) and section 7 (`auth_sessions`, `auth_refresh_tokens`,
`auth_invitations`, `auth_tokens`, `auth_attempts`).

## 5. API
Final version: docs/API.md section 7 (and section 0 for the Bearer token). FRONTEND_GUIDE section
12 describes the screens; ARCHITECTURE section 4 the module, its contracts with `property`, the
query filters, the demo rules and the settings.

## 6. Privacy and security
- **Never in logs or spans:** e-mails, tokens, passwords, cookies or IP addresses; only keyed
  hashes are stored. New scrubber markers: `refresh`, `magic`, `client_ip`, `ip_address`,
  `user_agent` (it already has `token`, `email`, `password`, `secret`, `cookie`).
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
| A4 | Invitations (new and existing accounts), registration, roles, ending a membership, closing an account, replace tenant, the first-chairman command | tests: nobody is created before an invitation is accepted; registration creates the account; an existing account accepts by link; the chairman invites only owners and staff; owners only tenants, at most 3 with pending ones; the owner never learns whether an e-mail exists; closing wipes personal data and notifications drops the bindings; the last chairman stays |
| A5 | Optional password | Argon2id; 15 characters; one generic error and timing; throttling without lockout; a change revokes other sessions; a reset through the e-mail link |
