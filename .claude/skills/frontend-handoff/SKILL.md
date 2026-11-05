---
name: frontend-handoff
description: Hand merged backend work over to the frontend. Writes tasks into the frontend repository's own backlog (docs/backlog/NNN-*.md plus its index) and copies the fresh contract (docs/openapi.json, docs/API.md, docs/FRONTEND_GUIDE.md) there, touching only docs/. Use when the owner asks to hand a task, a plan of work or the contract to the frontend (in any language). After a merged backend pull request that changes the client contract, only suggest a handoff; never run it without the owner's request.
---

# Frontend handoff

The owner works on the client in Google AI Studio, which is connected to the **frontend
repository**. AI Studio can only "sync git and read a file", so everything it needs must be in
that repository. This skill puts it there. The process is described for people in
docs/FRONTEND_GIT_WORKFLOW.md section 2 ("Tasks from the backend").

## Repositories and access

- Backend: this repository, branch `develop`. The contract is final only after the backend pull
  request is merged; never hand over an unmerged contract.
- Frontend: the repository in the Actions variable `FRONTEND_REPOSITORY` of this repository:
  `cd <this repo>; gh api repos/{owner}/{repo}/actions/variables/FRONTEND_REPOSITORY --jq .value`
  (the placeholders need the repository as the working directory). If that call
  fails, ask the owner; never guess the name from the docs (they only show an example). Its
  default branch is `main`.
- Auth as in the `ai-dev-zoomcamp-git-ops` skill: every `git` / `gh` call inside `bash -ic '...'`,
  `gh` with `export GH_TOKEN="$GITHUB_AI_DEV_ZOOMCAMP_TOKEN"`. The token can push to the frontend
  repository. This skill is the one exception to "act only on this repository" in the git-ops
  skill, and only for the paths listed below.
- **Clone without exposing the token.** Copy the credential helper of this repository as literal
  text, so `$GITHUB_AI_DEV_ZOOMCAMP_TOKEN` is expanded only by git at push time. Run the lines as
  written inside the single-quoted `bash -ic '...'` wrapper:

  ```
  h=$(git -C <this repo> config --get credential.https://github.com.helper)
  git -c credential.https://github.com.helper="$h" clone https://github.com/<owner/name>.git <scratchpad>/fe
  git -C <scratchpad>/fe config credential.https://github.com.helper "$h"
  ```

  Use the plain https URL, never a token in a URL. To check the clone, run
  `grep -c GITHUB_AI_DEV_ZOOMCAMP_TOKEN <scratchpad>/fe/.git/config` (expect 1); never print the
  config. Set `user.name` / `user.email` in the clone from this repository's config.

## What the skill may change in the frontend repository

Only these, nothing else (never `src/`, configs, lock files or the AI Studio workspace):

| Path | Content |
| :--- | :--- |
| `docs/openapi.json`, `docs/API.md`, `docs/FRONTEND_GUIDE.md` | Exact copies of the pinned backend commit, overwritten on every handoff |
| `docs/BACKEND_HANDOFF.md` | The last handed-over backend commit and a log (date, backend commit, items). It says that the originals and the files they link to (epics, workflow) live in the backend repository |
| `docs/backlog/NNN-<slug>.md` | New files only, one per task, in the frontend's template (`docs/backlog/_template.md`) |
| `docs/backlog/README.md` | Only new rows in the "Backlog Index" table (replace the placeholder row "No pending items yet" if present). Never change other rows, statuses or the text |
| Tag `backend-handoff/<backend short sha>` | On the handoff commit. Tags survive a force push of `main`, so the next run can tell whether the handoff is still there |

Never edit or delete an existing backlog item and never mark one done: the frontend owns its
backlog. A correction to a handed-over task is a new item that names the old one.

## Steps

0. **Before anything, ask the owner** to sync the AI Studio workspace with GitHub (push or pull
   all pending work). Unsynced AI Studio work is exactly what a later force push would use to
   overwrite `main`, dropping the handoff. Wait for the answer.
1. **Pin both sides.** `git fetch` here and pin `sha=$(git rev-parse --short origin/develop)`; use
   `git show $sha:<path>` and `$sha` everywhere below. Clone the frontend repository **fresh**
   into the scratchpad (delete an old clone first: leftover commits must never be pushed again).
2. **Check that the previous handoff survived.** Take the newest tag `backend-handoff/*` in the
   clone (`git tag -l 'backend-handoff/*' --sort=-creatordate`); no tag means a first handoff, so
   skip this step. Run `git merge-base --is-ancestor <tag> origin/main`. If it is not an ancestor,
   AI Studio pushed over it: compare the item files of the tagged commit with the `origin/main`
   tree, re-add only the missing ones (as new items, see step 5) and tell the owner.
3. **Find what changed for the client** since the last handed-over backend commit: the sha in the
   newest tag name (not `docs/BACKEND_HANDOFF.md`, which a force push can roll back). First
   handoff: compare the frontend's `docs/openapi.json` with this repository's:
   - `git log --oneline <last>..$sha -- docs/API.md docs/FRONTEND_GUIDE.md docs/openapi.json`;
   - new or changed paths and schemas between the two `openapi.json` files (a short Python
     comparison of `paths` and `components.schemas`);
   - what the frontend already built (grep its `src/` for the endpoints), so a task does not
     repeat finished work.
4. **Cut tasks** the way a person would review them: one item per user-visible feature (a screen
   or a flow), small enough for one AI Studio session. Ask the owner only if the cut is unclear.
5. **Write each item** (English, no competitor names) from `docs/backlog/_template.md`:
   - `NNN`: the next number after the highest one among the files in `docs/backlog/` and the
     index rows, zero-padded to 3 digits;
   - title `[NNN] [backend <epic>/<task>] <feature>`; `Status: TODO`; `Created: <today>`;
     `API Impact: Existing API`; an honest complexity; the backend commit `$sha` in the summary;
   - **Summary & goal:** who uses it and why, in product terms;
   - **Affected screens:** routes, views, stores, sidebar entries per role, locales. Name only
     paths that exist in the clone (check with `ls` / `grep`), e.g. `src/router/routes.js`,
     `src/views/...`, `src/locales/*.js`; a new file is named as a suggestion;
   - **API & data contract:** every endpoint with method, path, request, response fields, roles
     and error codes, pointing to the sections of the copied `docs/API.md` and
     `docs/FRONTEND_GUIDE.md` instead of pasting them whole; the types to add (e.g.
     `src/api/schema.d.ts`); the mock API to extend so the screen works without a backend;
   - **Privacy rules** that apply (badges only, never names or contacts, 404 means "not visible");
   - **Acceptance criteria** as checkboxes, ending with: every locale the app has; `bun run lint`
     and `npm run test` pass; no backend file is changed.
6. **Copy the contract**: the three files with `git show $sha:<path>`.
7. **Update `docs/BACKEND_HANDOFF.md`**: `$sha`, the date and the new items on top of the log.
8. **Guard before committing.** In the clone, stage the files above by name, then:
   - `git status --porcelain` and `git diff --cached --name-only` list only `docs/openapi.json`,
     `docs/API.md`, `docs/FRONTEND_GUIDE.md`, `docs/BACKEND_HANDOFF.md` and `docs/backlog/...`;
   - no tracked `docs/backlog/*.md` other than `README.md` is modified (`git diff --cached
     --diff-filter=M --name-only docs/backlog`), only added;
   - `git diff --cached docs/backlog/README.md` only adds index rows (and may remove the
     placeholder row);
   - anything else: stop and tell the owner.
9. **Confirm with the owner**: show the item titles and `git diff --cached --stat`, and push only
   after an explicit yes, unless the owner said beforehand to push without asking.
10. **Commit, tag and push**: one commit `docs(frontend): hand over <features> from backend
    $sha`, `git pull --rebase`; if the rebase brought new commits, check again that every new
    `NNN` is unique and rerun the step 8 guard on `git diff origin/main..HEAD`. Then
    `git push origin main`, then
    `git tag backend-handoff/$sha && git push origin backend-handoff/$sha`. Never force-push.
    Confirm the commit is on `origin/main`.
11. **Tell the owner** (in Russian, short): the item numbers and titles, and what to say in AI
    Studio: sync with GitHub first, then the implement phrase from the frontend backlog README
    with the item number, one item at a time.

## Notes

- The owner's local folder `docs/frontend/` here is theirs: never read it as a source, never
  stage it.
- During the `main` freeze, `develop` is not deployed, so the frontend works against its mock API;
  every item must keep the mock in step with the contract.
- A frontend version that uses a handed-over item must reach `main` here only after the backend
  change is on `main` (docs/FRONTEND_GIT_WORKFLOW.md section 2).
- If the frontend needs data the contract lacks, that is a `backend-request` issue in this
  repository (docs/FRONTEND_GIT_WORKFLOW.md), not something the handoff invents.
