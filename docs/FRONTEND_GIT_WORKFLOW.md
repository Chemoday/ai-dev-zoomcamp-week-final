# Frontend Git Workflow (Google AI Studio → GitHub → dev)

Rules for everyone who commits frontend code: Google AI Studio, the frontend agent and people.
The code rules are in [FRONTEND_GUIDE.md](FRONTEND_GUIDE.md) and the API contract is
[openapi.json](openapi.json); this file only covers repositories, commits and how a change reaches
dev.

```text
Google Stitch (mockup) ──► Google AI Studio (Build) ◄──two-way sync──► frontend repository
                                                                            │ Frontend sync workflow
                                                                            ▼
     this repository: branch frontend/sync-<sha> ──► PR into main ──► CI (lint, build, pytest)
                                                                            │ merge
                                                                            ▼
                                                                 automatic deploy to dev
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
| `package.json` and `package-lock.json` at the repository root | `npm ci` must install exactly what was tested |
| `npm run build` writes static files to `dist/` | The image copies `dist/`; the backend serves it at `/` |
| `npm run lint` and `npm test` exist and pass | CI runs them on every sync pull request |
| Base path `/`; Vue Router in HTML5 history mode | The backend answers unknown paths with `index.html` |
| API calls use relative URLs (`/api/v1/...`) | The same build works on localhost, dev and prod; no CORS |
| Vite dev proxy: `/api`, `/health`, `/docs` → `VITE_API_PROXY_TARGET` (default `http://localhost:8000`) | Local development against a local or the dev backend |

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

The **Frontend sync** workflow (*Actions → Frontend sync → Run workflow*, optionally with a commit
of the frontend repository; default: its latest `main`):

1. copies the frontend repository into `frontend/` without `.git`, `node_modules` and `dist`;
2. writes the synced commit into `frontend/.source-commit`;
3. pushes the branch `frontend/sync-<short sha>` and opens a pull request into `main` titled
   `feat(frontend): sync <short sha>`, with the frontend commit messages since the last sync;
4. CI runs on the pull request: frontend lint, tests and build, plus the backend `pytest`.

Rules for the pull request:

- **Never edit `frontend/` in this repository by hand.** The next sync overwrites it. Fix the code
  in AI Studio or the frontend repository and sync again.
- Merge with **squash**: the pull request title becomes one Conventional Commit on `main`.
- A sync pull request changes only `frontend/`. Backend changes go in their own pull requests,
  before the frontend that needs them.

## 4. Seeing a change on dev

| Way | When | Time |
| :--- | :--- | :--- |
| `npm run dev` with `VITE_API_PROXY_TARGET` set to a local backend (`docker compose up`) or the dev URL | Every layout change | Instant |
| Label `deploy-dev` on the sync pull request: CI deploys that pull request to dev | Show a finished screen before the merge | ~5 min |
| Merge the sync pull request into `main` | The screen is done | ~5 min, automatic |

Dev is a single environment: whatever was deployed last is what it shows, whether it came from a
pull request or from `main`. Do not use the `deploy-dev` label on a pull request that changes
database migrations. Prod is promoted manually (docs/DEPLOYMENT.md).

## 5. Branches of this repository

| Branch | What | Who |
| :--- | :--- | :--- |
| `main` | Always deployable; every merge deploys to dev | Merges only through pull requests with green CI |
| `feat/...`, `fix/...`, `docs/...` | Backend, CI and docs work, one task per branch | Backend agent |
| `frontend/sync-<sha>` | One frontend version, created by the Frontend sync workflow | Workflow |
| `demo/*` | Course demonstrations (on-call responder); never merged | Demo only |

The Frontend sync workflow, the `deploy-dev` label and serving the client from the backend are
tracked in issue
[#28 Frontend in CI, the Docker image and dev](https://github.com/Chemoday/ai-dev-zoomcamp-week-final/issues/28).
Until it is done, sections 3 and 4 describe the target, not what runs today.
