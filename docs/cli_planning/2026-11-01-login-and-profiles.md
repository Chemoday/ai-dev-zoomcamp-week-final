# 2026-11-01: What comes after notifications (login, unsubscribe, apartment profiles)

A record of a planning session with the coding agent (Claude Code CLI) after epic E1 reached N6.

## 1. Where we were
- E1 Notifications: in-app, e-mail, Telegram, WebPush, reading reminders and the clean-up are in
  `develop` (N1-N6). Only N7 (push to the native phone app) is left, and it waits for a login.
- There is no login: every environment runs in demo mode, where anyone can act as the chairman.
  Because of that, the external channels run nowhere yet (E1 10.10, docs/BACKLOG.md B-7).
- `main` is frozen while the course submission is under review: everything goes into `develop`.

## 2. Options considered
B-7 real login, B-3 database backups, E2 billing, E5 bulk import. **Chosen: B-7 as epic E7**,
because it unblocks real use of E1 and is the basis of security for a real association. B-3 stays
a must before the first real association.

## 3. Decisions

**Login (E7, docs/epics/E7_LOGIN.md)**
- Both an e-mail link (magic link) and an optional password. Smart-ID and the ID card come later
  as country plugins.
- Accounts by invitation only: the chairman invites owners and staff, and an owner invites tenants
  of their own apartment.
- Demo mode stays on everywhere until a separate decision.
- In the review of the page:
  - only the chairman invites directly (a CSV import comes with E5);
  - the chairman enters only the e-mail and the apartment number, and the person completes the
    registration;
  - a session ends after a week without a visit.
- After the CTO review of the page:
  - the chairman does not invite tenants, only owners;
  - family members get no access of their own ("they sort it out among themselves");
  - a password needs at least 15 characters (NIST SP 800-63B-4 for a single factor);
  - login e-mails in demo mode are allowed only on an environment behind a password gate (local,
    dev), never on the public prod.
- The CTO review also shaped the technical design (E7 page, section 3):
  - pending invitees are inactive until they complete the registration;
  - e-mail addresses are canonical;
  - a grace window lets two tabs refresh at once;
  - the `auth` module sends its own e-mails, after the commit;
  - closing an account notifies the other modules through a domain event;
  - an operator command creates the first chairman, and the last chairman is protected.

**Tenants**
- At most 3 active tenants per apartment, as a setting so it can grow (e.g. rooms rented in one
  flat). For now the owner manages them; letting a main tenant manage co-tenants is a later
  option.
- "Replace tenant" closes the old account and invites a new one. The owner first suggested a
  single tenant account whose e-mail changes; the agent pointed out that the account carries
  personal data and links (feed, Telegram, browsers, sessions, forum comments, preferences) that
  must not pass to the next person. The meter history belongs to the apartment and stays.

**Unsubscribe (E1/N8)**
- Every non-mandatory e-mail gets a one-click unsubscribe link that needs no login, plus
  `List-Unsubscribe` / `List-Unsubscribe-Post` (RFC 8058).
- Telegram gets `/stop`. Emergencies stay mandatory.

**Apartment profiles (E8, docs/epics/E8_APARTMENT_PROFILES.md)**
- The chairman and the board see apartment cards without names or contacts: residents as Owner /
  Tenant, the meters and the reading history. An accountant sees the history only.
- "Write" was first imagined as "send an e-mail". The agent recommended a private thread stored
  in the service, with only a "new message" notification sent outside: replies stay possible
  without revealing addresses, and the board keeps the history when the chairman changes. The
  owner agreed.

## 4. Order
1. E7 Login (A1 contract first, reviewed by the owner).
2. E1/N8 unsubscribe (small; needs no login, so it can run alongside).
3. E8 Apartment profiles.
Then N7 (FCM) and the decision to leave demo mode.
