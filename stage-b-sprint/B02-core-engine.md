# Prompt B02: Core AI Logic / Engine Agent

- **Time Budget:** 90 Minutes
- **Owner:** Member 1 (Backend)
- **Cut If Over Time:** Multi-step autonomous agent loops; fall back to a single well-engineered model call with structured JSON schema output.

---

```markdown
[PASTE FILLED SHARED CONTEXT BLOCK HERE FROM SHARED_CONTEXT.md]

ROLE: Core AI & Algorithms Engineer.
GOAL: Implement the primary business logic, model invocation pipeline, and data transforms strictly following `specs/contracts.md`.

INPUTS:
- `specs/contracts.md`
- Working scaffold from B01

INSTRUCTIONS:
1. Implement the central processing logic or LLM pipeline in `backend/app/services/core_engine.py` (or `.ts`).
2. Enforce strict JSON schema validation for model outputs (e.g., Pydantic model in Python / Zod in TypeScript).
3. Handle error cases: Model rate limits, timeouts (set an 8s timeout), and malformed outputs.
4. Provide an in-memory/canonical test verification (`scripts/test_core.py` or `.ts` integrated with test command) that runs against a sample input and asserts valid output format.
5. Strict Anti-Bloat Directive: Do NOT create throwaway runner scripts (`temp_test.py`), debug logs, or dumps (`output.json`). Do NOT introduce extra requirement files.

HANDOFF CONTRACT:
- Consumes: `specs/contracts.md`
- Produces: Tested core logic module ready to be connected to the API router.

ACCEPTANCE CRITERIA:
Done when running `python scripts/test_core.py` (or `bun/npm run test:core`) executes the full AI/algorithmic pass in under 5 seconds and prints formatted JSON matching `specs/contracts.md`.
```
