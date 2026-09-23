# Prompt B03: API & Endpoints Agent

- **Time Budget:** 60 Minutes
- **Owner:** Member 1 (Backend)
- **Cut If Over Time:** Persistent SQL database; use an in-memory dictionary or lightweight SQLite for demo session persistence.

---

```markdown
[PASTE FILLED SHARED CONTEXT BLOCK HERE FROM SHARED_CONTEXT.md]

ROLE: Backend API Integration Engineer.
GOAL: Connect the core logic from B02 into the HTTP router stubs created in B01, completing backend capabilities.

INPUTS:
- `specs/contracts.md`
- Core engine module from B02

INSTRUCTIONS:
1. Connect the core engine service to the actual API routes defined in `specs/contracts.md`.
2. Implement in-memory state or lightweight storage (SQLite/JSON file) to hold the last 20 demo runs.
3. Add a query parameter `?mock=true` or check header `x-demo-mock: true` to instantly return deterministic fixtures if live execution fails.
4. Log all incoming requests and outgoing payloads cleanly to stdout/console with timestamps for live debugging.
5. Strict Anti-Bloat Directive: Do NOT generate output dump files (`debug.log`, `test_dump.json`) or temporary curl test scripts. Output only to console.

HANDOFF CONTRACT:
- Consumes: B02 core logic, `specs/contracts.md`
- Produces: Full working backend server ready for frontend consumption.

ACCEPTANCE CRITERIA:
Done when a real curl request triggers the live engine and returns the expected schema, and the mock flag instantly returns seed fixtures.
```
