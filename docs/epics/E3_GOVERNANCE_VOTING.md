# E3. Governance and voting

- **Phase:** 3. **Status:** idea, decisions below. Detailed design before the first task.

## Decision: abstract voting in the core, verified voting in country plugins

Module `governance` (country-agnostic):
- A vote: questions, options (`FOR` / `AGAINST` / `ABSTAIN`, or a custom list of options), deadline,
  quorum rule, how a ballot is weighted (one per apartment, or by ownership share).
- Who may vote: owners with `can_vote` from `ApartmentMembership`. An apartment with several
  owners casts one vote (rule configurable: any owner, or all must agree).
- **Proxy voting:** an owner authorises another person (a tenant, a relative, a neighbour) to vote
  on their behalf; a legal-entity owner votes through its representative.
  `VotingProxy(apartment, principal_user, proxy_user, valid_from, valid_to, document)`.
- Board decisions without a meeting: the same vote, with board members as voters.
- Protocol (PDF) with results and comments; the person who confirms it is the protocol keeper.
- A tie can end with "no decision" (no forced lottery).

Country plugin protocol `VoteVerifier`:
- `countries.ee.asice_verifier`: the voter downloads the ballot PDF, signs it with ID-card,
  Mobile-ID or Smart-ID (free DigiDoc4 client), and uploads the `.asice` container. The plugin
  checks the signature, that the signed file hash equals the issued ballot hash, and that the
  signer's `subject_hash` matches the voter's identity. No paid Smart-ID API is needed.
- A signed proxy document is verified the same way.
- Without a country plugin a vote is "simple" (logged-in click), which is fine for informal polls.

## Depends on

- E1 Notifications (vote opened, deadline approaching, results).
- `core_user_identities.subject_hash` (exists since the MVP).
