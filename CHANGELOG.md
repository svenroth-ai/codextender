# Changelog

All notable changes are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project
uses [Semantic Versioning](https://semver.org/).

## [0.1.0]

First tagged release.

### Added

- Local Anthropic-compatible proxy on top of LiteLLM that sends Claude
  Code's requests to the Codex backend using the OAuth credentials of the
  local Codex CLI.
- Several Codex models behind one proxy, with client-side mapping of the
  `opus`, `sonnet` and `haiku` tiers and a default of `gpt-6.1-sol`.
- Background OAuth token refresh, including after resume from suspend.
- Support for Claude Code's non-streaming auto-mode classifier request.
- Real 1'050'000-token context window reported to Claude Code.
- Windows autostart (Startup folder) and manual restart scripts.
- `@svenroth-ai/codextender` npm wrapper.

### Fixed

- Billing header no longer breaks Codex prompt caching.
- System and developer role items are folded into `instructions`.
- UTF-8 output is forced to avoid a crash on Windows consoles.
