# Git flow

## Branches
| Branch | Role | Who merges into it |
|---|---|---|
| `main` | Stable releases only | **The owner only**, via PR `release` → `main` when release is stable |
| `release` | Release candidate; base for all new work | Claude or the owner, via PR from a work branch |
| `development` | Integration / testing | Claude or the owner, via PR from a work branch |
| `feat/…`, `fix/…`, `chore/…` | One roadmap item or change each | — |

## Lifecycle of a change
1. **Branch from `release`** (fetch first):
   `git fetch origin; git switch -c feat/FEAT-01-scheduled-backups origin/release`
   - `feat/<ID>-<slug>` for roadmap features (FEAT-xx), `fix/<ID>-<slug>` for fixes (FIX-xx),
     `chore/<slug>` for docs, tooling and QUAL-xx work.
   - One roadmap item per branch.
2. **Commit** with the Claude co-author trailer (below). Run the tests before pushing.
3. **Push** and **PR the branch into `development`**. Merge once tests pass.
4. **PR the same branch into `release`**. Merge after it has been checked on `development`.
5. **`release` → `main`**: only the owner opens and merges this PR, when `release` is stable.

Because the branch starts from `release`, the release PR carries only that item's commits, even
when `development` holds other unreleased work.

## Merge style
Use **merge commits** (`gh pr merge --merge`), not squash or rebase. The same branch lands in two
bases, and squashing would give `development` and `release` different commits for the same change,
so later PRs would conflict. Delete the work branch only after both PRs are merged.

## Commit messages
Imperative subject (≤ 72 chars), English, blank line, optional body, then the trailer:

```
Add session cookies and failed-login lockout

Replaces per-request Basic auth with a signed session cookie (FIX-01).

Co-Authored-By: Claude <noreply@anthropic.com>
```

Every commit Claude writes or helps write carries the `Co-Authored-By: Claude` trailer. PR
descriptions name the roadmap ID and summarize the test results.

## Rules for Claude
- May: create branches, commit, push, open PRs into `development` and `release`, and merge them.
- **Never** open, approve or merge a PR into `main`, and never push to `main`, even when asked
  in passing. Releasing to `main` is the owner's call.
- Never force-push `development`, `release` or `main`.

## Tooling
PRs and merges use the GitHub CLI (`gh`); log in once with `gh auth login` and `gh auth setup-git`.
