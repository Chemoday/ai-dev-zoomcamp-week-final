# E3. Governance and voting

- **Phase:** 2-3. **Status:** part A (informative polls) designed with the owner on 2026-11-05,
  tasks V1-V3 below; part B (official decisions) is designed later, after its own planning
  session (section 4). **Epic issue:**
  [#97](https://github.com/Chemoday/apartment-association-platform/issues/97).
- **Goal:** the board asks the residents or the owners a question and gets an honest, countable
  answer; later, the association takes formal decisions in the app, with the weight the law gives
  them.

This page is the design. Each task writes its part of the binding contract (docs/SCHEMA.md,
docs/API.md, docs/ARCHITECTURE.md, docs/FRONTEND_GUIDE.md) in its first commit; where they differ,
the contract wins.

## 1. Owner decisions

From 2026-11-04 (local idea L-6):
- Two kinds of polls. **Official:** the basis of formal decisions (owners only, one vote per
  apartment, quorum, deadline, signatures where the country requires them). **Informative:** quick
  statistics, not binding and labelled as such everywhere. Answer types: one or several options,
  free text, number, date.
- Informative polls come first; official decisions reuse their core.

From 2026-11-05:
- **Only the board** (the chairman and board members) creates polls. A resident who wants one
  proposes it in the forum or in a message to the board.
- **The board chooses per poll** who answers (all residents, or owners only) and whether there is
  one answer per apartment or one per person.
- **One answer per apartment:** any eligible member of the apartment may answer and change the
  answer until the poll closes; the last answer counts. The apartment's members see that the
  apartment has answered and what.
- **Results:** everyone sees totals only (per option, and how many answered out of how many);
  the board also reads the free-text answers, without authors. Nobody sees how a given apartment
  or person answered.
- **Notifications:** only "a new poll" to everyone who may answer, on their channels (can be
  switched off). No reminder before the deadline and no results notification.
- **A question has two settings** (owner's refinement): how it is answered (pick one option, pick
  several, or the person's own value) and the type of the value (text, number or date). So the
  options themselves can be dates ("which Saturday for the clean-up") or numbers, shown in each
  person's language and counted as dates and numbers.

## 2. The legal frame (Estonia)

Read from the Apartment Ownership and Apartment Associations Act (KrtS), official English
translation of 2018 (RT I, 13.03.2014, 3; in force 01.01.2018). The act has been amended since
(for example a minimum answer term for decisions without a meeting), so every number below is to
be checked against the current Estonian text before part B and lives in the Estonian country
plugin, never in the core.

| Rule | KrtS | What the core must be able to express |
| :--- | :--- | :--- |
| One vote per apartment ownership; the articles may give one vote per owner, or votes by the share in the common ownership | § 22 (1) | A voting unit (apartment or person) and a weight per unit |
| Joint owners of one apartment exercise their rights jointly | § 15 (1) | One ballot per apartment, with a rule for who may cast it |
| The voters are fixed on the day of the vote | § 22 (2) | A snapshot of the electorate when the vote opens |
| An owner does not vote on matters concerning themselves | § 22 (3) | Excluding a unit from one question |
| A representative with a written authorisation may vote | § 22 (5) | A ballot cast on behalf of someone (proxy) |
| A decision without a meeting: the draft goes to all owners with a term; silence counts as against; adopted with more than half of the votes of all owners unless the articles require more | § 21 (1)-(3) | Majority over the whole electorate, not over the ballots cast; non-answers counted as against |
| The record of voting names the owners who voted in favour and states dissenting opinions | § 21 (4)-(5) | Named ballots in part B (unlike informative polls), immutable after closing |
| Some decisions need more than half of all votes held by owners of more than half of the shares; structural changes need the owners' agreement | §§ 9 (3), 36, 38, 39 | A majority rule per question, also a double majority (votes and shares) |
| A meeting has a quorum with more than half of the votes and of the shares | § 20 (2) | A quorum rule (for recording meeting decisions) |
| A resolution may be contested within 60 days | § 29 (3) | Ballots and results kept unchanged after closing |

## 3. Part A: informative polls

### 3.1 Module `governance`
A new module (country-agnostic, as planned for E3), with its own tables `gov_*`. It reads
apartments and memberships only through `PropertyService` and DTOs; communication is not needed.

### 3.2 A poll
- **Questions**, in order, each with two settings and a "required" flag:
  - `answer_mode`: `SINGLE_CHOICE`, `MULTIPLE_CHOICE` (with a minimum and a maximum number of
    choices) or `OWN_VALUE` (the person types their own value);
  - `value_type`: `TEXT`, `NUMBER` (a decimal string, optional bounds) or `DATE` (a calendar date).
  - Choice questions have options whose values are of the question's `value_type` (a text label,
    a number or a date); the client renders numbers and dates in the reader's language.
  - New value types (for example a time, or yes/no) are added to `value_type` without changing
    the rest.
- **Target:** the same as posts: the whole building, an entrance, or chosen apartments
  (`TargetScope`, and the shared targeting rule `apartment_in_target`).
- **Eligibility:** `RESIDENTS` (every active member of a targeted apartment) or `OWNERS` (active
  `OWNER` memberships only).
- **Unit:** `APARTMENT` (one answer per apartment) or `PERSON` (one per eligible person; a person
  with two targeted apartments answers once).
- **Kind:** `INFORMATIVE` (the only kind in part A; `DECISION` is reserved for part B).
- **Deadline** (`closes_at`) and lifecycle `DRAFT -> OPEN -> CLOSED`, or `CANCELLED`:
  - a draft is visible to the board only and fully editable; only a draft can be deleted;
  - opening fixes the questions and takes the **electorate snapshot**;
  - a poll is closed when `closes_at` has passed (no background job: "closed" is computed) or
    when the board closes it early; the board may extend `closes_at` while the poll is open;
  - an open poll can be cancelled; it stays visible as cancelled, its answers are not shown;
  - after closing nothing changes any more.

### 3.3 The electorate snapshot
On opening, the poll stores its units: the targeted apartments that have at least one eligible
member (unit `APARTMENT`), or the eligible people (unit `PERSON`), each with a weight of 1. It is
the denominator of "12 of 30 apartments answered".
- With unit `APARTMENT`, who may answer is checked at answer time: any current eligible member of
  an apartment in the snapshot (a tenant who moved in after the opening may answer for the
  apartment). With unit `PERSON`, only the people in the snapshot answer.
- An apartment without an eligible member at opening is not in the snapshot (informative polls
  count who can answer). Part B counts every apartment ownership instead (section 4).

### 3.4 Answering
- One **ballot** per unit, with its answers per question. Answering again replaces the answers
  (the last one counts); every version is kept in a revision history that is never shown in
  part A (part B needs it).
- The ballot records who cast it (internal, never returned) and, for unit `APARTMENT`, for which
  apartment.
- `GET` of a poll returns the caller's own ballot: for unit `APARTMENT`, the apartment's ballot,
  whoever in the apartment cast it ("your apartment answered"), never who.

### 3.5 Who sees what
- The poll: the board always (drafts included); everyone else once it is open, through its
  target (any active member of a targeted apartment, also when not eligible to answer: a tenant
  sees an owners-only poll but cannot answer). Another building: never (404).
- **Results** (totals): the board at any time after opening; everyone else who sees the poll
  after it closed. Per question: for choice questions the count per option (options in their
  order); for `OWN_VALUE` questions with `NUMBER` the count, minimum, maximum and average, with
  `DATE` the count per date, with `TEXT` the count only; and the participation (units answered /
  units in the snapshot).
- **Free-text answers:** the board only, without authors, in a stable order that does not follow
  the answer time (so the order reveals nothing).
- Never returned: who answered, how an apartment or a person answered. Small numbers can still
  hint (one answer from a small entrance); the board's poll form says so.

### 3.6 Notifications (E1)
- Event type `POLL_OPENED`: to everyone who may answer when the poll opens (members of the
  snapshot apartments who are eligible, or the snapshot people), never the board member who
  opened it; not mandatory; default on for every channel.
- Text without the title or the questions (the system texts rule of E1): "The board started a
  poll. Please answer by 12.11.2026." The link opens the poll (a new subject type `poll`).
- Driven by a durable domain event `PollOpened` (ids only).

### 3.7 API sketch (`/api/v1/governance`)
| Endpoint | Who | What |
| :--- | :--- | :--- |
| `POST /polls` | board | Create a draft with its questions |
| `PUT /polls/{id}` | board | Replace a draft |
| `DELETE /polls/{id}` | board | Delete a draft (`409` otherwise) |
| `POST /polls/{id}/open` | board | Open: snapshot, `PollOpened` |
| `POST /polls/{id}/close`, `PATCH /polls/{id}` (`closes_at` only), `POST /polls/{id}/cancel` | board | Close early, extend, cancel |
| `GET /polls`, `GET /polls/{id}` | everyone who sees it | List (open first, then the newest), detail with `can_answer` and the caller's ballot |
| `PUT /polls/{id}/ballot` | eligible | Answer or change the answer |
| `GET /polls/{id}/results` | board; others after closing | Totals; free texts for the board |

### 3.8 Shaped for part B on purpose
- The electorate snapshot with a **weight** per unit (always 1 in part A).
- **One ballot per unit** with the casting person and the apartment it is cast for, and its
  **revision history**.
- **Results as a pure function** of the snapshot and the ballots (`governance/policies.py`); part
  B adds majority and quorum rules there.
- The `kind` column, the status machine, and **immutability after closing**.
- Nothing in part A depends on names, so part B decides separately where names appear (the
  record of voting).

## 4. Part B: official decisions (designed later)

Adds on top of part A, after a planning session (recorded in docs/cli_planning/) on: the change
of an apartment's owner (docs/BACKLOG.md B-10), the shares in the common ownership (a new
apartment field), elections of the board (B-9), and the legal weight of an electronic vote.
- Kind `DECISION`, questions `FOR` / `AGAINST` / `ABSTAIN`, owners only, every apartment
  ownership in the electorate (also without an app user), non-answers counted as against.
- Weights by the articles: per apartment, per owner, or by share; joint owners cast one ballot;
  proxies (`VotingProxy(apartment, principal_user, proxy_user, valid_from, valid_to, document)`);
  excluding an owner from a question that concerns them.
- Majority rules per question (over all votes, double majority, unanimity) and quorum; country
  defaults from a plugin.
- The record of voting with the names of those in favour and dissenting opinions, sent to all
  owners; a protocol PDF; kept unchanged for the challenge period.
- Country plugin protocol `VoteVerifier`: `countries.ee.asice_verifier` checks a ballot signed
  with ID-card, Mobile-ID or Smart-ID (an `.asice` container from the free DigiDoc4 client): the
  signature, that the signed file hash equals the issued ballot hash, and that the signer's
  `subject_hash` matches the voter's identity. A signed proxy document is verified the same way.
  Without a country plugin a vote is "simple" (a signed-in click).
- Board decisions without a meeting: the same vote, with board members as voters.
- A tie ends with "no decision" (no forced lottery).

## 5. Tasks (part A)

| Task | What | Done when |
| :--- | :--- | :--- |
| V1 ([#94](https://github.com/Chemoday/apartment-association-platform/issues/94)) | Module `governance`: polls, questions, options, the lifecycle, the electorate snapshot, ballots with history, visibility; the contract | the board creates, opens, closes, extends and cancels a poll; eligible people (or one per apartment) answer and change; others see but cannot answer; another building gets 404; tests |
| V2 ([#95](https://github.com/Chemoday/apartment-association-platform/issues/95)) | Results: totals, participation, the board's free-text answers | totals after closing for everyone, at any time for the board; free texts only for the board and without authors; nothing reveals who answered what; tests |
| V3 ([#96](https://github.com/Chemoday/apartment-association-platform/issues/96)) | `POLL_OPENED` notifications and the `poll` subject; frontend handoff | everyone who may answer is notified once, never the author; texts in en, ru, et; tests |

## 6. Open

- Part B (section 4) and its planning session.
- Comments on a poll (today: the forum), a CSV export of results, and polls for the board only
  (board decisions).
- Whether informative polls should allow a "results visible while open" option per poll.
- With unit `PERSON` the voters are fixed at opening, but who sees the poll still follows the
  current memberships: a voter who moved out no longer sees it. Part B decides whether
  visibility follows the snapshot too (the record of voting must reach every voter).

## Depends on

- E1 Notifications (`POLL_OPENED`), E7 (accounts), the shared targeting rule of property.
- Part B: B-10, B-9, and `core_user_identities.subject_hash` (exists since the MVP).
