# AGENTS.md

## Agent skills

### Issue tracker

Issues live in GitHub Issues on nickknissen/bilkatogo, via the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

Default labels: needs-triage, needs-info, ready-for-agent, ready-for-human, wontfix. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: `GLOSSARY.md` and `docs/adr/` at the repo root. See `docs/agents/domain.md`.

## Testing

Before calling any change done, run `uv run python scripts/check.py`: CI's lint and test jobs (ruff format, ruff, ty, `release.py check`, pytest, and pytest again on Python 3.12).

The library supports Python 3.12 and up; only `custom_components/` and `tests/ha` may need 3.14, which Home Assistant requires. In `src/`, a module whose annotations name a class defined later or imported under `TYPE_CHECKING` needs `from __future__ import annotations`; ty does not catch the `NameError` this raises on 3.12 and 3.13.

`uv run pytest` skips `tests/ha` when Home Assistant can't be installed, which is always the case on Windows. Before calling a change to `custom_components/` or a model's serialised fields done, run `scripts/ha-test.sh` (from Windows: `wsl.exe -e bash scripts/ha-test.sh`); it runs CI's `ha-test` job, including the diagnostics snapshot.

Capturing real Bilka responses as fixtures, or checking the library against the live site: see `docs/agents/capturing.md`.

## Releasing

The PyPI package and the Home Assistant integration share one version (`docs/adr/0001`):
`pyproject.toml`, the `version` in `custom_components/bilkatogo/manifest.json` and its
`bilkatogo==X.Y.Z` requirement must always match. CI runs `uv run python release.py check`
and fails when they differ. Never edit these versions by hand; use `release.py`.

Only release when the maintainer asks. From a clean, up-to-date `main`:

```sh
uv run python release.py publish X.Y.Z
```

This bumps all three versions, refreshes `uv.lock`, commits `Release vX.Y.Z`, pushes `main` and
creates the GitHub release `vX.Y.Z` with generated notes. Publishing the release triggers
`.github/workflows/publish.yml`, which checks that the tag matches the package version, builds
with `uv build` and uploads to PyPI through trusted publishing (no token). The Home Assistant
Marketplace picks up the same GitHub release as the integration version.

`uv run python release.py bump X.Y.Z` only edits the files, for trying a bump locally.

One-time setup, done by the maintainer: a PyPI trusted publisher for the `bilkatogo` project
with owner `nickknissen`, repository `bilkatogo` and workflow `publish.yml`.
