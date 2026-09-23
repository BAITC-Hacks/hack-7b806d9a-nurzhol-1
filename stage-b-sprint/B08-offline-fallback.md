# Prompt B08: Fallback & Offline Mock Mode Agent

- **Time Budget:** 30 Minutes (Run concurrently with B06)
- **Owner:** Member 1 (Backend Lead)
- **Cut If Over Time:** Do NOT cut. This is your primary insurance policy against demo crashes.

---

```markdown
[PASTE FILLED SHARED CONTEXT BLOCK HERE FROM SHARED_CONTEXT.md]

ROLE: Reliability & Contingency Engineer.
GOAL: Build an indestructible, zero-dependency offline fallback mode so the demo is immune to venue Wi-Fi failure and LLM rate limits.

INPUTS:
- `data/seeds.json` from B07
- `specs/contracts.md`

INSTRUCTIONS:
1. Implement a global interceptor in both the frontend API client layer and backend router.
2. When `DEMO_MOCK_MODE=true` or when the backend service is unreachable, the frontend immediately serves `seeds.json` payloads with realistic 500-800ms artificial delays to simulate live thinking.
3. Include an unobtrusive toggle switch in the UI footer or triggered via keyboard shortcut (`Ctrl + Shift + M`) to flip between Live and Mock mode on the fly.
4. Verify the entire application can execute and complete the hero demo with Wi-Fi completely disabled.
5. Strict Anti-Bloat Directive: Use purely native code and existing dependencies for mock interception. Do NOT install heavy mocking libraries or create duplicate fixture files outside `data/seeds.json`.

HANDOFF CONTRACT:
- Consumes: `data/seeds.json`, frontend/backend client code
- Produces: Indestructible offline presentation guarantee.

ACCEPTANCE CRITERIA:
Done when disconnecting your computer from the internet allows the demo flow to run smoothly from start to finish without console errors or UI alerts.
```
