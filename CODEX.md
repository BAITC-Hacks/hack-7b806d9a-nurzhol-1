# 📜 AI AGENT CODEX: REPOSITORY HYGIENE & ANTI-BLOAT DIRECTIVES

> **MANDATORY PRE-FLIGHT DIRECTIVE:**
> Every AI agent (Codex, Cursor, Claude Code, OpenCode) must read and strictly obey these rules BEFORE writing code, editing files, or installing dependencies in this repository.
> Keeping the repository clean, minimal, and free of junk files is a zero-tolerance policy.

---

## 🚫 1. ZERO EXTRA REQUIREMENTS & DEPENDENCY BLOAT

- **Single Dependency Manifest:**
  - For Node / TypeScript / Web: Use **ONLY** the single root `package.json` (or designated monorepo workspace package).
  - For Python: Use **ONLY** one canonical dependency file (`requirements.txt` OR `pyproject.toml`). Never split them.
- **Strictly Prohibited Files:**
  - ❌ NEVER create `requirements-dev.txt`, `requirements_test.txt`, `requirements-prod.txt`, or `reqs.txt`.
  - ❌ NEVER generate secondary lockfiles (e.g., do not introduce `yarn.lock` or `pnpm-lock.yaml` if `package-lock.json` is used).
  - ❌ NEVER switch or introduce alternate package managers (`pipenv`, `poetry`, `pip-tools`) unless pre-existing.
- **Minimal Dependencies First:**
  - Leverage built-in standard library utilities (`fetch`, `crypto`, `pathlib`, `json`, `os`, `unittest.mock`) before adding external libraries.
  - Never install new third-party packages without verifying that existing project dependencies cannot solve the problem.

---

## 🧹 2. ZERO REPO TRASH & SCRATCHPAD POLLUTION

- **No Scratchpad / Disposable Files:**
  - ❌ NEVER create temporary runner scripts (`temp_test.py`, `scratch.ts`, `test_run.sh`, `dummy.js`).
  - ❌ NEVER output debug dumps into the repo (`output.json`, `debug.log`, `result.txt`, `dump.sql`).
  - All tests, experiments, and verifications must be executed in-memory, via test suites, or directly via CLI command arguments.
- **No Unsolicited Markdown Documentation:**
  - ❌ NEVER generate `NOTES.md`, `SUMMARY.md`, `CHANGES.md`, `TODO.md`, `EXPLANATION.md`, or `WALKTHROUGH.md`.
  - Provide summaries and explanations directly in the conversation chat or PR description, never as repo files.
- **Single Canonical Environment & Configs:**
  - Keep ONLY one `.env.example`.
  - ❌ NEVER create `.env.test`, `.env.local.example`, `.env.staging`, or `.env.backup`.
  - Do NOT create redundant linter/formatter configs (`.eslintrc.*`, `.prettierrc`, `.babelrc`) unless explicitly requested.

---

## ⚡ 3. LEAN IMPLEMENTATION & REPO DISCIPLINE

- **Flat, Direct Architecture:**
  - Avoid speculative wrapper layers, unnecessary microservices, and superfluous helper classes. Keep architecture simple, clean, and directly functional.
- **No Dead Code:**
  - Remove all commented-out code blocks and temporary mock placeholders once actual logic is added.
- **Clean Execution Guarantee:**
  - If a test or build tool produces local artifacts or cache files, ensure they are cleaned up or properly gitignored.

---

## ✅ 4. PRE-FLIGHT & PRE-COMMIT CHECKLIST

Before concluding any task or commit, verify:
1. `git status` shows **NO** untracked junk files, logs, or unrequested markdown files.
2. No duplicate requirement or configuration files were created.
3. Only the minimal necessary dependencies were touched.
