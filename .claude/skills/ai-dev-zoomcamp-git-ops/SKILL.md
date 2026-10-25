---
name: ai-dev-zoomcamp-git-ops
description: Use for ANY direct GitHub operation against this project's own GitHub repository — the one checked out at this project's root and pointed to by its `origin` remote — commit/push, branches, pull requests, issues, releases, GitHub Actions runs/deploys, or repo settings. Scoped strictly to that one repository; never target any other repo even if the configured token has broader access. This skill is the entry point whenever the user asks to commit, push, sync, deploy, or otherwise manipulate this repo on GitHub.
---

# ai-dev-zoomcamp git ops

## Scope

This skill file is designed to be copied as-is into any sibling repo that
follows this same course-project pattern (one repo per homework week). It
must always operate on **the repository this copy lives in** — determined
from this project's own `origin` remote and local checkout path, never
from a hardcoded repo name, owner/repo string, or absolute path left over
from another week's copy. If you find repo-specific text here that
doesn't match `git remote get-url origin` for this checkout, treat it as
stale and correct it before relying on it.

## Authentication

- The GitHub token lives in `$GITHUB_AI_DEV_ZOOMCAMP_TOKEN`, persisted in
  `~/.bashrc`.
- `~/.bashrc` only exports it for **interactive** shells (it has the
  standard `case $- in *i*) ;; *) return;; esac` guard near the top). Every
  `git` or `gh` command in this skill MUST be wrapped like this or the
  token will not be in scope and auth will fail:

  ```
  bash -ic '<command>'
  ```

- This repo's local `.git/config` already has a `credential.https://github.com.helper`
  configured to read that variable at push time. The token is never
  written to disk and never needs to be passed explicitly.
- `gh` is NOT logged in and does not use that helper: every `gh` command
  must be prefixed with `export GH_TOKEN="$GITHUB_AI_DEV_ZOOMCAMP_TOKEN";`
  inside the `bash -ic` wrapper. The installed `gh` is old (e.g.
  `gh issue close` has no `--comment`; use `gh issue comment` first).
- Never print, echo, or otherwise include the raw token value in any
  command, file, or output.
- The `bash -ic` subshell starts in `$HOME`, not the repo directory —
  every command must `cd` back into this project's root first. Use the
  actual local path of *this* checkout (the working directory shown in
  your environment context) — never a hardcoded path copied from another
  week's repo, e.g.:

  ```
  bash -ic 'cd /absolute/path/to/this/checkout; git push -u origin main'
  ```

## Commit message and PR title conventions

Every commit message and PR title in this repo MUST follow
[Conventional Commits](https://www.conventionalcommits.org/):

```
<type>(<scope>): <short summary>

<optional body — explain why, not what>
```

- **Types**: `feat` (new capability), `fix` (bug fix), `docs` (docs only),
  `refactor` (restructuring, no behavior change), `test` (tests only),
  `chore` (tooling/deps/config), `style` (formatting only), `perf`
  (performance). Pick the one that matches the *primary* effect of the
  change.
- **Scope** is optional but encouraged when it adds clarity, e.g.
  `feat(api):`, `fix(chores):`.
- **Summary**: imperative mood ("add", not "added"/"adds"), lowercase
  after the type, no trailing period, aim for under ~70 characters.
- **Body**: blank line after the summary, then explain the *why* — the
  diff itself already shows the *what*.
- **Split unrelated changes into separate commits by type/concern**
  rather than one large commit — e.g. a feature, an unrelated bugfix it
  exposed, and a docs update are three commits (`feat: ...`, `fix: ...`,
  `docs: ...`), not one. Don't over-split trivial, tightly-coupled
  changes (e.g. a feature and the one-line test it needs).
- PR titles follow the same `<type>(<scope>): <summary>` format as the
  commit message.

**Quoting gotcha**: never use an apostrophe or contraction (e.g.
"caller's", "doesn't") in a commit message body when it will be passed
through the `bash -ic '<command>'` wrapper below — the single-quoted
wrapper terminates at the first `'` inside the message, silently
truncating/corrupting the command. Reword around it (e.g. "the
requesting user" instead of "the user's") rather than trying to escape
the quote.

## Committing and pushing

1. `bash -ic 'git status'` — review what changed before staging anything.
2. Stage specific files by name (not `git add -A` / `git add .`) unless the
   user clearly wants everything staged.
3. Commit with a Conventional Commits message (see above), in a HEREDOC to
   avoid quoting issues:
   ```
   bash -ic 'git commit -m "$(cat <<"EOF"
   <type>(<scope>): <summary>

   <body>
   EOF
   )"'
   ```
4. Push: `bash -ic 'git push -u origin <branch>'` (only use `-u` the first
   time a new branch is pushed).
5. Confirm the push succeeded (check command exit status / `git status`).

## Branch management

- Create and switch: `bash -ic 'git checkout -b <branch-name>'`
- Switch: `bash -ic 'git checkout <branch-name>'`
- Push a new branch: `bash -ic 'git push -u origin <branch-name>'`
- Delete a local branch: `bash -ic 'git branch -d <branch-name>'`
- Delete a remote branch: `bash -ic 'git push origin --delete <branch-name>'`

## Other GitHub operations

The token is scoped to this repository, so any `gh` subcommand works
against it the same way, using the same `bash -ic '<command>'` wrapper
for auth. Run these from inside this project's directory so `gh` resolves
the correct repo implicitly — don't pass an explicit `--repo`/`-R` flag
pointing at a different repo:

- Pull requests: `gh pr create`, `gh pr merge`, `gh pr view`, etc. — the
  `--title` must follow the Conventional Commits format above.
- Issues: `gh issue create`, `gh issue list`, etc.
- Releases: `gh release create`, `gh release upload`, etc.
- Actions / deploys: `gh workflow run <name>`, `gh run watch`, `gh run view`.
- Anything not covered by a dedicated `gh` subcommand: `gh api ...`.

## Safety rules

- This skill only ever acts on the repository at this project's root (its
  `origin` remote) — never `cd` into, or pass a `--repo`/`-R` flag
  pointing at, a different week's repo, even though the configured token
  may have access to more than one.
- Never force-push (`--force` / `-f`) unless the user explicitly asks for it.
- Confirm with the user before: pushing directly to `main`, deleting any
  branch (local or remote) or the repo itself, merging a PR, publishing a
  release, triggering a deploy/workflow, changing repo visibility/settings,
  or any other action visible outside this local checkout.
- Only commit files the user would expect to be committed — flag anything
  that looks like a secret or credential before staging it.
- Treat every write operation as irreversible-until-proven-otherwise:
  state what you're about to do and get explicit confirmation first,
  rather than assuming the token's scope means broad license to act
  unprompted.
