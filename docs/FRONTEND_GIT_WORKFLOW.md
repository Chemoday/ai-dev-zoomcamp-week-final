# Frontend Git Workflow (Google AI Studio → GitHub → dev)

Rules for everyone who commits frontend code: Google AI Studio, the frontend agent and people.
The code rules are in [FRONTEND_GUIDE.md](FRONTEND_GUIDE.md) and the API contract is
[openapi.json](openapi.json); this file only covers repositories, commits and how a change reaches
dev.

```text
Google Stitch (mockup) ──► Google AI Studio (Build) ◄──two-way sync──► frontend repository
                                                                            │ Frontend sync workflow
                                                                            ▼
     this repository: branch frontend/sync ──► PR into main ──► CI (lint, test, build, pytest)
                                                     │ label deploy-dev          │ merge
                                                     ▼                           ▼
                                               preview on dev          automatic deploy to dev
```

## 1. Two repositories

| Repository | Owner of the content | Contains |
| :--- | :--- | :--- |
| **Frontend repository** (separate, e.g. `Chemoday/aap-frontend`) | Google AI Studio and you | The client app at the repository root, exactly as AI Studio builds it |
| **This repository** (`ai-dev-zoomcamp-week-final`) | Backend, CI, deploy | Backend, docs, and a copy of the client in `frontend/` |

**Never connect Google AI Studio to this repository.** AI Studio treats the whole connected
repository as its app, rewrites files to make the project build and pushes them with a force push
(see the official guide *Sync your AI Studio apps with GitHub*). Connected here, it would overwrite
the backend, the docs and the history of `main`.

## 2. Rules for the frontend repository

**Commits.** AI Studio pushes with commit messages it writes itself, so the messages there are not
checked. When you commit by hand, use [Conventional Commits](https://www.conventionalcommits.org/)
with the scope `frontend`:

```text
feat(frontend): add the documents view
fix(frontend): keep the meter value a string in the confirmation popup
```

**One change per push.** Sync to GitHub after one view or one fix, not after a day of changes.
Every push becomes a reviewable part of the next sync pull request.

**Build contract.** CI and the Docker image rely on these, whatever AI Studio generates:

| Rule | Why |
| :--- | :--- |
| `package.json` and `bun.lock` at the repository root (AI Studio uses bun) | `bun install --frozen-lockfile` installs exactly what was tested |
| `bun run build` writes static files to `dist/` | The image copies `dist/`; the backend serves it at `/` |
| `bun run lint` and `npm run test` exist and pass (tests use `node:test`, so they run on Node) | CI runs them on every sync pull request |
| Base path `/`; Vue Router in HTML5 history mode | The backend answers client routes with `index.html` |
| API calls use relative URLs (`/api/v1/...`) | The same build works on localhost, dev and prod; no CORS |
| `VITE_BACKEND_URL` for `bun run dev`: set, the dev server proxies `/api` to that backend; unset, it uses its built-in mock API | Local development against a local backend, the dev URL, or no backend at all |
| `index.html` is a static file, configuration comes from the API | The backend never rewrites the client, so the client can move to its own server later |

**Not part of the app:** the AI Studio workspace (`.aistudio/`) and the mockups in
`public/assets/aistudio/`. The sync leaves them out, so they never reach this repository or a
build. Keep app files out of these two folders.

**Never commit:** `node_modules/`, `dist/`, `.env` files (only `.env.example`), API keys.
AI Studio templates often read `GEMINI_API_KEY` / `process.env.API_KEY`: the client does not
call Gemini, so remove that code and the variable, and do not put any key in the bundle. Anything
in the built JavaScript is public.

**Content.** Code, comments, file names and commit messages are English. UI texts live in locale
files, not in the components.

**The backend is the source of truth.** Use only fields that exist in `docs/openapi.json`. Data
that the mockup shows but the API does not return is either static demo content with a visible
`DEMO` badge, or a request for the backend (next rule). Never change backend code from the
frontend.

**Missing backend data.** Open an issue in **this** repository with the label `backend-request`:
the screen, the mockup element, the fields you need and a suggested endpoint. The backend answers
with an API change and a new `docs/openapi.json`; the frontend then regenerates its types.

## 3. Bringing a frontend version into this repository

The **Frontend sync** workflow (`.github/workflows/frontend-sync.yml`) runs every 15 minutes and
by hand (*Actions → Frontend sync → Run workflow*, optionally with a branch or commit of the
frontend repository; default: its `main`). When the frontend has a version that `main` does not:

1. it copies the frontend repository into `frontend/`, without `.git`, `node_modules`, `dist`,
   `.env` files, `.aistudio/` and `public/assets/aistudio/`;
2. writes the synced commit into `frontend/.source-commit`;
3. force-pushes the single branch `frontend/sync` (one commit on top of `main`) and opens, or
   updates, the pull request `feat(frontend): sync <short sha>` with the frontend commit messages
   since the version on `main`, and the label `deploy-dev`;
4. CI runs on the pull request: frontend lint, tests and build, the backend `pytest`, the image,
   and then the deploy of that pull request to dev.

So a frontend push is on dev about 20 minutes later at most (15 for the schedule, ~5 for CI),
without anyone merging. Run the workflow by hand to skip the wait.

Configuration (once): repository variable `FRONTEND_REPOSITORY` (`owner/name`) and secret
`FRONTEND_SYNC_TOKEN`, a token that reads the frontend repository and writes this one. A personal
token is required: pushes and pull requests made with the default `GITHUB_TOKEN` start no CI.

Rules for the pull request:

- **Never edit `frontend/` in this repository by hand.** The next sync overwrites it. Fix the code
  in AI Studio or the frontend repository and sync again.
- Merge with **squash**: the pull request title becomes one Conventional Commit on `main`.
- A sync pull request changes only `frontend/`. Backend changes go in their own pull requests,
  before the frontend that needs them.

## 4. Seeing a change on dev

| Way | When | Time |
| :--- | :--- | :--- |
| `bun run dev` with `VITE_BACKEND_URL` set to a local backend (`docker compose up`) or the dev URL | Every layout change | Instant |
| The sync pull request (label `deploy-dev`, set by the workflow): every update goes to dev | Check a version before it reaches `main` | ≤ 20 min after the push |
| Merge the sync pull request into `main` | The version is accepted | ~5 min, automatic |
| `docker compose up --build` in this repository | The whole app locally: the image builds `frontend/` and serves it at http://localhost:8000 | A few minutes |

Dev is a single environment: whatever was deployed last is what it shows, whether it came from a
pull request or from `main`. The label `deploy-dev` works on any same-repository pull request; do
not use it on one that changes database migrations. Prod is promoted manually
(docs/DEPLOYMENT.md).

## 5. How the backend serves the client

`src/core/frontend.py`, active only when the image contains `frontend/dist/index.html`:

| Request | Answer |
| :--- | :--- |
| `/assets/*` (hashed build files) | The file, `Cache-Control: public, max-age=31536000, immutable` |
| Any other `GET`/`HEAD` path without a file extension (`/`, `/documents`, `/forum/posts/…`) | `index.html`, `Cache-Control: no-cache` |
| A path with a file extension | The file from `dist/` if it exists, else a JSON `404` (a stale tab never gets HTML instead of a script) |
| `/api/…`, `/health`, `/docs`, `/redoc`, `/openapi.json`, other methods | The backend as before: an unknown API path stays a JSON `404` |

**Moving the client to its own server later** (e.g. Caddy or nginx serving `dist/`, with `/api`,
`/health` and `/docs` proxied to the backend) needs no client change, as long as two rules hold:
`index.html` stays a static file (no server-side templating), and API changes stay backward
compatible (add fields; remove or rename only after a period where both work), because then the
client and the backend are deployed separately and can briefly run different versions.

## 6. Branches of this repository

| Branch | What | Who |
| :--- | :--- | :--- |
| `main` | Always deployable; every merge deploys to dev | Merges only through pull requests with green CI |
| `feat/...`, `fix/...`, `docs/...` | Backend, CI and docs work, one task per branch | Backend agent |
| `frontend/sync` | The next frontend version; force-pushed by the Frontend sync workflow | Workflow |
| `demo/*` | Course demonstrations (on-call responder); never merged | Demo only |

The Frontend sync workflow, the `deploy-dev` label and serving the client from the backend were
added in
[#28 Frontend in CI, the Docker image and dev](https://github.com/Chemoday/ai-dev-zoomcamp-week-final/issues/28).
