# @svenroth-ai/codextender

`npx @svenroth-ai/codextender@latest` — run Claude Code on your OpenAI/ChatGPT
Codex-plan subscription models. See the main
[codextender README](https://github.com/svenroth-ai/codextender) for what it
does and why.

This package is a thin Node wrapper, not the proxy itself: it finds a Python
3.11+ interpreter, keeps a dedicated venv at `~/.codextender/venv` up to date
via `pip install --upgrade git+https://github.com/svenroth-ai/codextender.git`,
then execs the real `codextender` command with your arguments. There is no
PyPI package — this is the only distribution channel.

```bash
npx @svenroth-ai/codextender@latest --port 4000
```

All arguments pass straight through to `codextender`. Pass `--no-upgrade` to
skip the update check on a run where you know you're already current.
