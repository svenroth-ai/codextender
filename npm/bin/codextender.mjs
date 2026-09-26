#!/usr/bin/env node
/**
 * codextender.mjs — `npx @svenroth-ai/codextender@latest` entry point.
 *
 * There is deliberately no PyPI package (see the project's own memory record
 * of that decision) — this wrapper is the ONE command customers run. It:
 *   1. resolves a real Python 3.11+ interpreter,
 *   2. ensures a dedicated venv at ~/.codextender/venv exists,
 *   3. `pip install --upgrade`s codextender straight from GitHub into it
 *      (a courtesy freshness check, never a gate — see step 4's fallback),
 *   4. execs the venv's own `codextender` console script with all args
 *      passed through untouched.
 *
 * `--no-upgrade` skips step 3 when already installed, for a faster restart
 * loop while iterating locally. A first-ever run always installs regardless
 * of that flag — there's nothing to skip to yet.
 */
import { spawnSync } from "node:child_process";
import { resolvePython, installHint } from "../lib/python.mjs";
import { ensureVenv, installOrUpgrade, isInstalled, venvCodextender } from "../lib/venv.mjs";

const rawArgs = process.argv.slice(2);
const noUpgrade = rawArgs.includes("--no-upgrade");
const passthroughArgs = rawArgs.filter((a) => a !== "--no-upgrade");

function fail(message) {
  console.error(`[codextender] ${message}`);
  process.exit(1);
}

const python = resolvePython();
if (!python) {
  fail(`no Python 3.11+ found on PATH.\n  ${installHint()}`);
}

const venv = ensureVenv(python.cmd);
if (!venv.ok) {
  fail(venv.error);
}

const alreadyInstalled = isInstalled();
if (!alreadyInstalled || !noUpgrade) {
  console.error(`[codextender] ${alreadyInstalled ? "checking for updates" : "installing"} (pip install --upgrade from GitHub)...`);
  const install = installOrUpgrade();
  if (!install.ok) {
    if (alreadyInstalled) {
      // Courtesy check only — an existing install still works offline.
      console.error(`[codextender] WARNING: ${install.error} — continuing with the existing install.`);
    } else {
      fail(install.error);
    }
  }
}

const result = spawnSync(venvCodextender(), passthroughArgs, { shell: false, stdio: "inherit" });
if (result.error) {
  fail(`failed to launch codextender: ${result.error.message}`);
}
process.exit(result.status ?? 1);
