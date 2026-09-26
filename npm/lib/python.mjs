/**
 * python.mjs — locate a real Python 3.11+ interpreter on PATH.
 *
 * Unlike `claude`/`npm`/`npx` on Windows, `python`/`python3` resolve to real
 * `.exe` files (or a Store app-execution-alias `CreateProcess` follows fine),
 * never a `.cmd` shim — so the CVE-2024-27980 shim problem that forces
 * shipwright-webui's bootstrapper to carry `win32-spawn.mjs` does not apply
 * here. A plain `spawnSync(name, ..., { shell: false })` is sufficient.
 */
import { spawnSync } from "node:child_process";

const CANDIDATES = process.platform === "win32" ? ["python", "python3"] : ["python3", "python"];

function versionOf(cmd) {
  const res = spawnSync(cmd, ["--version"], { shell: false, encoding: "utf8" });
  if (res.error || res.status !== 0) return null;
  const out = `${res.stdout ?? ""}${res.stderr ?? ""}`;
  const m = /Python (\d+)\.(\d+)/.exec(out);
  if (!m) return null;
  return { major: Number(m[1]), minor: Number(m[2]) };
}

/**
 * @returns {{ cmd: string, major: number, minor: number } | null}
 */
export function resolvePython() {
  for (const cmd of CANDIDATES) {
    const v = versionOf(cmd);
    if (v && (v.major > 3 || (v.major === 3 && v.minor >= 11))) {
      return { cmd, ...v };
    }
  }
  return null;
}

export function installHint() {
  return process.platform === "win32"
    ? "install Python 3.11+ from https://www.python.org/downloads/ (NOT the Microsoft Store stub) and make sure it's on PATH"
    : "install Python 3.11+ (e.g. `brew install python@3.11`) and make sure it's on PATH";
}
