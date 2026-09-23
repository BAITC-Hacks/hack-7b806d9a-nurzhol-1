# Prompt B06: End-to-End Integration & Edge Case Agent

- **Time Budget:** 60 Minutes
- **Owner:** Member 1 & 2 (Backend & Frontend pair)
- **Cut If Over Time:** Complex WebSocket/SSE stream parsing; fall back to standard HTTP POST with a 2-second simulated stepper.

---

```markdown
[PASTE FILLED SHARED CONTEXT BLOCK HERE FROM SHARED_CONTEXT.md]

ROLE: Full-Stack Integration Engineer.
GOAL: Connect the polished frontend (B05) to the live backend (B03) and test edge cases under demo conditions.

INPUTS:
- Backend server (B03)
- Polished frontend (B05)
- `specs/contracts.md`

INSTRUCTIONS:
1. Replace frontend mock state with actual `fetch()` / API client calls to the backend endpoints.
2. Implement graceful fallback: If the backend fails or takes > 6 seconds, automatically catch the error, switch to mock data seamlessly, and display a subtle "Demo Fixture Active" badge without breaking the UI.
3. Test 3 sequential runs to verify memory leaks, unhandled promise rejections, or UI freezing do not occur.
4. Verify CORS and environment variables are properly wired across the full loop.
5. Strict Anti-Bloat Directive: Run all tests and verifications in-memory or via CLI. Do NOT produce temporary scratchpad test scripts (`temp_integration.ts`), test output dumps, or secondary lockfiles.

HANDOFF CONTRACT:
- Consumes: B03 backend, B05 frontend
- Produces: End-to-end working software product.

ACCEPTANCE CRITERIA:
Done when triggering the action in the UI sends a network request to the backend, receives real AI/backend processing, and renders the result dynamically in the UI.
```
