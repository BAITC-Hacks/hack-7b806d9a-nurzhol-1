# Prompt B01: Project Scaffolding Agent

- **Time Budget:** 30 Minutes
- **Owner:** Member 1 (Backend / Lead)
- **Cut If Over Time:** Extra linting configs, complex database migrations, CI pipelines.

---

```markdown
[PASTE FILLED SHARED CONTEXT BLOCK HERE FROM SHARED_CONTEXT.md]

ROLE: Lead DevOps & Boilerplate Specialist.
GOAL: Bootstrap a clean, runnable full-stack workspace with mock endpoints, dependencies, and environment files in under 30 minutes.

INPUTS:
- `specs/contracts.md`

INSTRUCTIONS:
1. Initialize the monorepo / directory structure specified in `specs/contracts.md`.
2. Strict Anti-Bloat Directive (per `CLAUDE.md` / `CODEX.md`):
   - Single Manifest: Use ONLY the single root `package.json` (or single `requirements.txt` for backend). NEVER create `requirements-dev.txt`, `requirements_test.txt`, or split dependency files.
   - Zero Secondary Configs: Create ONLY one canonical `.env.example` (no `.env.test`, `.env.local.example`, or `.env.staging`).
   - Zero Throwaway Scripts: Do NOT add temporary bootstrap scripts or throwaway runners.
3. Install minimal essential dependencies:
   - Frontend: Next.js or Vite, Tailwind CSS, Lucide Icons, clsx/tailwind-merge.
   - Backend: FastAPI (Python) or Node/Express/Hono, CORS middleware, dotenv, model SDKs.
4. Configure CORS to allow full local cross-origin communication (`*` for dev).
5. Implement stub endpoints for every route defined in `specs/contracts.md` returning static mock data directly.
6. Verify that one dev command boots both frontend and backend without errors.

HANDOFF CONTRACT:
- Consumes: `specs/contracts.md`
- Produces: Runnable repo skeleton at `localhost:3000` (frontend) and `localhost:8000` (backend).

ACCEPTANCE CRITERIA:
Done when running the start command boots both servers, the frontend displays a basic shell, and `curl http://localhost:8000/api/health` returns `{"status": "ok"}`.
```
