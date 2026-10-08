/**
 * venv.mjs — a persistent, isolated venv at ~/.codextender/venv that
 * `pip install --upgrade git+https://...` targets.
 *
 * WHY a dedicated venv instead of `pip install --user` into the system
 * Python: this wrapper runs `pip install --upgrade` on every invocation
 * (mirroring `npx pkg@latest`'s always-fresh model) — doing that against
 * whatever Python happens to be first on PATH would silently mutate a
 * user's other Python projects' global/user site-packages. A dedicated venv
 * makes every codextender install fully disposable (delete the folder to
 * reset) and never touches anything else on the machine.
 */
import { spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import { homedir } from "node:os";
import path from "node:path";

export const VENV_DIR = path.join(homedir(), ".codextender", "venv");

const BIN_DIR = process.platform === "win32" ? "Scripts" : "bin";
const EXE = process.platform === "win32" ? ".exe" : "";

export function venvPython() {
  return path.join(VENV_DIR, BIN_DIR, `python${EXE}`);
}

export function venvCodextender() {
  return path.join(VENV_DIR, BIN_DIR, `codextender${EXE}`);
}

/** @returns {{ ok: true } | { ok: false, error: string }} */
export function ensureVenv(pythonCmd) {
  if (existsSync(venvPython())) return { ok: true };
  // nosemgrep: javascript.lang.security.detect-child-process.detect-child-process -- interpreter found by us, fixed args, no shell
  const res = spawnSync(pythonCmd, ["-m", "venv", VENV_DIR], { shell: false, stdio: "inherit" });
  if (res.error || res.status !== 0) {
    return { ok: false, error: `failed to create venv at ${VENV_DIR}` };
  }
  return { ok: true };
}

const REPO = "git+https://github.com/svenroth-ai/codextender.git";

/** @returns {{ ok: true } | { ok: false, error: string }} */
export function installOrUpgrade() {
  const res = spawnSync(
    venvPython(),
    ["-m", "pip", "install", "--upgrade", "--quiet", REPO],
    { shell: false, stdio: "inherit" },
  );
  if (res.error || res.status !== 0) {
    return { ok: false, error: `pip install --upgrade ${REPO} failed` };
  }
  return { ok: true };
}

export function isInstalled() {
  return existsSync(venvCodextender());
}
