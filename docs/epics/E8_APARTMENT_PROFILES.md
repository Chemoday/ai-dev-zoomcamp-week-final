# E8. Apartment profiles

- **Phase:** 2. **Status:** idea with owner decisions (2026-11-01); detailed design after E7
  (login), which it builds on.
- **Goal:** the board can look after an apartment (its residents, meters and history) and write to
  it, without seeing anyone's name, e-mail or login.

## Owner decisions (2026-11-01)
- **Who sees the cards:** the chairman and the board. An accountant sees the meter history only.
- **What a card shows:**
  - the apartment number, entrance and area;
  - its residents as "Owner" / "Tenant", without names, e-mails or logins;
  - the meters and the reading history across periods.
- **"Write" is a private thread stored in the service, not an e-mail:**
  - The board and the apartment's members talk in a thread only they can see.
  - A new message notifies through E1 ("new message from the board", with a link and without the
    text, like every notification except emergencies).
  - Nobody sees anyone's contact data, and the board keeps the history when the chairman changes.
  - Why not plain e-mail: a reply would either be lost (a `noreply` sender) or reveal the
    addresses, and the history would live in one person's mailbox.
- **My residents:** an owner's page with the apartment's tenants: invite, resend, replace, remove
  (built on E7 task A4).

## Sketch
- **Apartment card:** a read model in `property` (apartment, members by relation) plus
  `meters` (meters and the history per apartment, reusing the history and matrix queries). It is
  combined in the router layer or a small read service, and modules never import each other's
  models.
- **Private thread:** a new post type in `communication`, visible only to the members of one
  apartment and the board. Replies are the existing comments, and E1 gets one more event type for
  "new message".

## Open for the E8 design
- Contact data: docs/SCHEMA.md says `core_users.phone` is visible only to the chairman and the
  board, while this page shows no contacts on the cards. Decide whether the board may see a phone
  number (e.g. for an emergency in the apartment), and if so, with an audit entry.

## Later
- "Reveal the real data with a reason" plus an audit log, if the board needs names for legal
  matters (meetings, debts).
