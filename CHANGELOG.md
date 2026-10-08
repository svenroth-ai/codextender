# Changelog

All notable changes are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project
uses [Semantic Versioning](https://semver.org/).

## [0.2.0] - 2026-10-08

### Added

- Claude tier aliases (`opus`, `sonnet`, `haiku`) are mapped to Codex
  models on the client side, so each tier can use a different model.
- A clear error that names the environment variable to set when a request
  uses an unknown model.
- CI: lint, a smoke test that checks the LiteLLM patches still apply
  (Ubuntu and Windows), dependency audit, Semgrep, Gitleaks and CodeQL.
- Dependabot version updates, a model-based review of Dependabot PRs, and
  auto-merge for Dependabot patch and minor updates once all checks pass.
- `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, `SECURITY.md`, issue and PR
  templates.

### Changed

- The default model is `gpt-6.1-sol`.

### Fixed

- A header line Claude Code changes on every request no longer breaks
  Codex's prompt cache.

## [0.1.0] - 2026-09-28

First release.

### Added

- Local Anthropic-compatible proxy on top of LiteLLM that sends Claude
  Code's requests to the Codex backend using the OAuth credentials of the
  local Codex CLI.
- Several Codex models behind one proxy.
- Background OAuth token refresh, including after resume from suspend.
- Support for Claude Code's non-streaming auto-mode classifier request.
- Real 1'050'000-token context window reported to Claude Code.
- Windows autostart (Startup folder) and manual restart scripts.
- `@svenroth-ai/codextender` npm wrapper.

### Fixed

- System and developer role items are folded into `instructions`.
- UTF-8 output is forced to avoid a crash on Windows consoles.
