# E8. Apartment profiles

- **Phase:** 2. **Status:** designed with the owner (2026-11-04, 2026-11-05); P1 done, tasks
  P2-P4 below. Builds on E7 (login) and E1 (notifications). **Epic issue:**
  [#82](https://github.com/Chemoday/ai-dev-zoomcamp-week-final/issues/82).
- **Goal:** the board can look after an apartment (its residents, meters and history) and write to
  it, without seeing anyone's name, e-mail or login.

This page is the design. Each task writes its part of the binding contract (docs/SCHEMA.md,
docs/API.md, docs/ARCHITECTURE.md, docs/FRONTEND_GUIDE.md) in its first commit; where they differ,
the contract wins. Background: the planning session of 2026-11-01
(docs/cli_planning/2026-11-01-login-and-profiles.md).

## 1. Owner decisions

From 2026-11-01:
- **Who sees the cards:** the chairman and the board. An accountant sees the meter history only.
- **What a card shows:**
  - the apartment number, entrance and area;
  - its residents as "Owner" / "Tenant", without names, e-mails or logins;
  - the meters and the reading history across periods.
- **"Write" is a private thread stored in the service, not an e-mail:**
  - The board and the apartment's members talk in a thread only they can see.
  - A new message notifies through E1 (with a link and without the text, like every notification
    except emergencies).
  - Nobody sees anyone's contact data, and the board keeps the history when the chairman changes.
  - Why not plain e-mail: a reply would either be lost (a `noreply` sender) or reveal the
    addresses, and the history would live in one person's mailbox.
- **My residents:** an owner's page with the apartment's tenants: invite, resend, replace, remove.
  The backend is E7 task A4 (done); only the frontend page is left.

From 2026-11-04:
- **Threads are built on the forum model:** a new post type whose target is one apartment, and
  replies are the existing comments.
- **Both directions:** the board writes to an apartment, and a resident writes to the board.
- **Visible to the board and to everyone attached to the apartment** (owners and tenants alike).
- **A new resident sees only the threads started after they moved in.** A tenant who moves in does
  not read the previous tenant's conversations with the board (e.g. about a debt). Former residents
  lose access when their membership ends. The board sees every thread.
- **The board is notified quietly:** a message from an apartment reaches the chairman and every
  board member, by default only in the app (no e-mail, Telegram or push). Each board member can
  turn the external channels on in their notification settings. Residents get a normal
  notification on every channel by default.
- **Every message notifies** (no "one notification until read"): these threads are low volume.

From 2026-11-05:
- **The board gets its own micro-forum** (task P4, after P2): topics that only the chairman and
  the board see. It is the base for a later building passport and maintenance calendar: handover
  notes, where the keys are, contractors, and the calendar's reminders for the board. The
  accountant does not see it.
- **P2 lays the ground for it:** who sees a post is decided by its type, so a board topic is one
  more type with its own rule, not a special case.

## 2. Apartment cards (task P1)

- `GET /api/v1/property/apartments` (chairman, board): every apartment of the building with its
  number, entrance, floor, area and the number of active owners, tenants and family members.
  Sorted by entrance, then by number as people read it (2 before 10).
- `GET /api/v1/property/apartments/{apartment_id}` (chairman, board): the same fields plus the
  residents, each as `{relation_type, since}`. No user id, name, e-mail or phone, and no badge:
  the card never depends on a resident's privacy choice. An apartment of another building is
  `404 APARTMENT_NOT_FOUND`.
- `GET /api/v1/meters/apartments/{apartment_id}/readings` (chairman, board, accountant, and the
  apartment's own members): the apartment's meters with one reading per period, newest period
  first, at most 24 periods. Each reading carries its value, consumption and source
  (`AUTO_ESTIMATED` marks an estimate). The history belongs to the apartment, so a new resident
  also sees older readings. The same series later feeds the resident's 12-month chart at no extra
  backend cost.
- The router layer combines nothing: the frontend calls property for the card and meters for the
  readings. Modules never import each other's models.
- **No contacts on the card.** Whether the board may see a phone number in an emergency, with an
  audit entry, stays open (section 6).

## 3. Board <-> apartment threads (task P2)

- **Post type `APARTMENT_THREAD`.** `target_scope = APARTMENT_LIST` with exactly one apartment
  (a database check). The title is the subject; replies are comments.
- **Who starts one:**
  - the board (chairman or board member) writes to any apartment of the building and speaks as
    the board (badge "Board");
  - a resident writes to the board from one of their own apartments (the target is that
    apartment) and is shown by their badge in that apartment, under their privacy setting.
  - An accountant who is not a resident of the apartment cannot start or read threads.
- **Who writes as whom** (decided by the server, not by the client), for starting a thread and
  for replies alike: a member of the thread's apartment writes from that apartment; anyone else
  who sees the thread (the board) writes as the board. A board member who lives in the apartment
  therefore starts and answers its threads as a resident (and is notified as one, section 4);
  another board member answers for the board.
- **Who sees a thread:** the chairman and the board always; a user through an active membership
  in the thread's apartment that started no later than the thread (`started_at <= created_at`).
  A member who wrote in the thread from that apartment (started it or replied) keeps seeing it
  with any active membership in that apartment (writing as the board does not count):
  reopening a membership that was ended by mistake sets `started_at` to now, and the resident
  must not lose the threads they took part in.
- **A thread to an apartment without active members is rejected** (`422
  APARTMENT_HAS_NO_RESIDENTS`): with the "since they moved in" rule, nobody but the board could
  ever read it.
- **Threads are a record:** they are never deleted or edited and always take replies
  (`comments_enabled` must be true), like official notices.
- **Visibility by post type.** Each post type has one visibility rule (emergencies and notices:
  their target; forum topics: the whole building; threads: the rule above; board topics in P4:
  the board). The feed, the post, its comments and the acknowledgement all use that one rule.
- **Not in the forum feed.** `GET /posts` without `post_type` leaves threads out.
  `GET /posts?post_type=APARTMENT_THREAD` is the "Messages" inbox, newest activity first; the board
  can add `apartment_id` to see one apartment's threads (from its card). Reading and replying use
  the existing `GET /posts/{id}` and `POST /posts/{id}/comments`.
- **`last_activity_at`** (new field of every post): the time of the newest comment, or of the post
  itself without comments. The inbox sorts by it.

## 4. Notifications for threads (task P3)

- Communication publishes a durable domain event `ApartmentMessagePosted` for a new thread and for
  every reply: post id, comment id (none for the thread itself), building, apartment, author, the
  thread's `created_at`, and whether the author wrote as the board.
- Two new event types, so the board's quiet default is an ordinary preference:

| Event type | Recipients | in_app | email | telegram | webpush |
|---|---|---|---|---|---|
| `APARTMENT_MESSAGE` | the apartment's members who see the thread | on | on | on | on |
| `APARTMENT_MESSAGE_TO_BOARD` | the chairman and the board | on | off | off | off |

- Recipients are everyone who sees the thread, except the author:
  - a message from the board notifies the apartment's members (not the other board members);
  - a message from a resident notifies the board and the apartment's other members.
  - A board member who is also a member of the apartment gets `APARTMENT_MESSAGE` only, once.
- Texts (system, `messages.csv`, no message text on any channel): "New message from the board",
  "New message in your apartment's conversation with the board" (a co-resident wrote), and for the
  board "New message from apartment 12". The link opens the thread (link kind `post`).
- Not mandatory: both types can be turned off per channel, and the e-mail carries the N8
  unsubscribe link.

## 5. Board micro-forum (task P4)

- **Post type `BOARD_TOPIC`:** visible to the chairman and the board only, never to the accountant
  or residents. Someone who leaves the board loses access at once; the topics stay with the board,
  so a new chairman inherits them.
- **Not in the forum feed:** `GET /posts?post_type=BOARD_TOPIC` lists them; replies are comments.
- **Groundwork for the passport and the calendar:** the calendar will later publish its
  reminders for the board as board topics, and the first, text-only building passport (keys,
  contacts, handover notes) can live in them.
- Designed in detail when P4 starts. Open:
  - how authors appear to colleagues (all of them would read "Board", which is useless inside
    the board; the privacy guardrail still applies);
  - whether the chairman may delete a topic, like a forum topic, or topics are a record;
  - notifications for a new topic or reply (likely the quiet in-app default of
    `APARTMENT_MESSAGE_TO_BOARD`).

## 6. Tasks

| Task | What | Done when |
|---|---|---|
| P1 ([#79](https://github.com/Chemoday/ai-dev-zoomcamp-week-final/issues/79)) | Apartment cards: the apartment list and card in `property`, the apartment readings in `meters`, the contract and the frontend handoff | the board sees cards without people's data; an accountant gets the readings but not the cards; a resident gets only their own apartment's readings; tests |
| P2 ([#80](https://github.com/Chemoday/ai-dev-zoomcamp-week-final/issues/80)) | Threads: `APARTMENT_THREAD` (migration, checks), visibility with the "since they moved in" rule, who writes as whom, the inbox filter, `last_activity_at` | a new tenant does not see older threads; a former tenant loses access; the accountant sees none; the forum feed has no threads; tests |
| P3 ([#81](https://github.com/Chemoday/ai-dev-zoomcamp-week-final/issues/81)) | Thread notifications: the domain event, the two event types with their defaults, recipients and texts (en, ru, et) | a resident's message reaches the board in the app only by default and the co-residents on all channels; the author is never notified; tests |
| P4 ([#84](https://github.com/Chemoday/ai-dev-zoomcamp-week-final/issues/84)) | Board micro-forum: `BOARD_TOPIC` on the visibility rules of P2 | only the chairman and the board see board topics; the accountant and residents get 404; topics are not in the forum feed; tests |

The "My residents" page is frontend work on the A4 endpoints (docs/FRONTEND_GUIDE.md section 12).

## 7. Open

- Contact data: docs/SCHEMA.md says `core_users.phone` is visible only to the chairman and the
  board, while the cards show no contacts. Decide whether the board may see a phone number (e.g.
  for an emergency in the apartment), and if so, with an audit entry.
- A limit on new threads per apartment and day, if residents ever flood the board.

## Later

- "Reveal the real data with a reason" plus an audit log, if the board needs names for legal
  matters (meetings, debts).
- Closing a thread, and "one notification until read" if the threads get busy.
