# Contributing to codextender

Thanks for your interest. codextender is a small project; contributions
are welcome, but please keep them focused.

## Before you start

- Typos and docs fixes: open a PR directly.
- Bug fixes: open an issue first, or link an existing one.
- New features or new LiteLLM patches: open an issue first to agree on the
  approach.
- Security problems: do not open an issue, see [SECURITY.md](SECURITY.md).

Please follow the [Code of Conduct](CODE_OF_CONDUCT.md).

## Setup

```bash
git clone https://github.com/svenroth-ai/codextender.git
cd codextender
pip install -e .
codextender --port 4000
```

Requires Python 3.11+ and a working Codex CLI login (`~/.codex/auth.json`).

## Verifying a change

There is no automated test suite. Verify against the real thing: start the
proxy, send a request (curl or a Claude Code session), and read the
response. Run `uvx ruff check .` before opening a PR. If you touch a
`*.ps1` script, check it against the Startup-folder constraint in
[docs/architecture.md](docs/architecture.md).

## Rules that matter

- **LiteLLM stays pinned** to an exact version in `pyproject.toml`. A
  version bump is its own change and must re-verify every patch in
  `src/codextender/patch.py`.
- **New patches** call LiteLLM's real code unmodified, then correct one
  thing at a public method boundary. No forking of LiteLLM internals.
- **Docs are part of the change.** If behavior, defaults, endpoints or
  install steps change, update `docs/` and the README in the same PR.
  Docs describe current behavior, not history.
- **Style:** English in code and docs, no em dashes, Swiss number notation
  (1'050'000 for thousands, a period for decimals).

## Commits and PRs

- Use [Conventional Commits](https://www.conventionalcommits.org/)
  (`feat:`, `fix:`, `docs:`, `chore:`).
- One logical change per PR. Describe what you ran to verify it and what
  the response looked like.
