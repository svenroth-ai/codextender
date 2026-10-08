# Security Policy

## Supported versions

Only the latest release and the `master` branch receive fixes.

## Reporting a vulnerability

Please do not report vulnerabilities through public issues. Use
[GitHub Security Advisories](https://github.com/svenroth-ai/codextender/security/advisories/new),
which opens a private channel with the maintainer.

Include a description, the affected file or flag, minimal steps to
reproduce, and the impact you see. This is a solo-maintainer project:
expect an acknowledgment within a week, and a fix as soon as reasonably
possible.

## Scope

In scope: this repository's code, including the proxy, the LiteLLM
patches, the PowerShell scripts and the npm wrapper.

Out of scope: vulnerabilities in LiteLLM, Claude Code, or the Codex CLI
(report those upstream), and issues that require an attacker who already
controls your machine or your `~/.codex/auth.json`.

## Things to know

- The proxy reads the OAuth tokens your Codex CLI stores in
  `~/.codex/auth.json` and keeps them in memory.
- Run it on localhost only. Do not expose it on a public interface: anyone
  who can reach the port can spend your Codex quota.
