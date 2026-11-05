# Contributing

Pull requests, bug fixes, translations and country modules are welcome.

## How we work

- Read [AGENTS.md](AGENTS.md) first: module boundaries, privacy guardrails and the change
  workflow apply to people and AI agents alike.
- One issue, one branch, one pull request. Commit messages and pull request titles follow
  [Conventional Commits](https://www.conventionalcommits.org/) (`feat(meters): ...`).
- Any schema or API change is documented in `docs/` before it is implemented; `pytest` and
  `ruff check` must pass.
- Code, comments, docs and commit messages are in English. UI and notification texts live in the
  locale files (`src/modules/notifications/messages.csv` on the backend).
- A pull request that uses AI assistance keeps its `Co-Authored-By` trailers: we prefer honesty
  about how the code was written.

## Contributor License Agreement

The project is source-available under the [LICENSE](LICENSE) (Elastic License 2.0 with
additional permissions), and its licensor also offers commercial and regional partner licenses.
So that a contribution never blocks either, every pull request must confirm this agreement
(the checkbox in the pull request template). By submitting a contribution (a pull request,
patch or commit) you confirm that:

1. The contribution is your own original work, or you otherwise have the right to submit it
   under these terms, and it does not knowingly infringe anyone's rights. If your employer has
   rights to work you create, it has allowed this contribution or waived those rights.
2. Third-party code in a contribution is marked with its source and its license, and that
   license allows the uses in 4 and 5.
3. You keep the copyright in your contribution.
4. You grant the licensor of this project, Aleksandr Kuhhar, and the licensor's successors and
   assigns (for example a company the licensor transfers the project to) a perpetual,
   worldwide, non-exclusive, no-charge, royalty-free, irrevocable license to use, reproduce,
   prepare derivative works of, publicly display, publicly perform, distribute and sublicense
   your contribution, under the project's public license and under any other license the
   licensor offers, including commercial and regional partner licenses, and to change the
   project's license in the future. This license may be transferred with the project.
5. You grant the same persons a perpetual, worldwide, non-exclusive, no-charge, royalty-free,
   irrevocable patent license to make, have made, use, sell, offer to sell, import and
   otherwise transfer your contribution and the project, for the patent claims you can license
   that your contribution alone, or its combination with the project, would infringe.
6. To the extent the law allows, you waive, or agree not to assert, moral rights in a way that
   would block these uses, except the right to be credited. Your contribution stays credited
   to you in the git history.

A pull request without the ticked checkbox is not merged.

## Regional partners

If you build a country module or a translation for a new market and want to offer hosting for
associations there, open an issue or contact the repository owner about a regional partner
license.
