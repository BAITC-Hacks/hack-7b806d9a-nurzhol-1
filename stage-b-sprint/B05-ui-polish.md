# Prompt B05: Interactive Polish & Demo-Flow Agent

- **Time Budget:** 60 Minutes
- **Owner:** Member 2 (Frontend)
- **Cut If Over Time:** Micro-animations and sound effects; maintain simple pulse loaders and clean badge color switches.

---

```markdown
[PASTE FILLED SHARED CONTEXT BLOCK HERE FROM SHARED_CONTEXT.md]

ROLE: Frontend UX Polish & Motion Specialist.
GOAL: Wire the user interaction loop, add animated state transitions, and create a 1-click "Run Demo" button that orchestrates the Hero Moment.

INPUTS:
- Frontend layout from B04
- `specs/problem_wedge.md` (Hero Moment specs)

INSTRUCTIONS:
1. Implement a prominent "Demo Scenarios" dropdown (Scenario A: Standard, Scenario B: Edge Case, Scenario C: Extreme).
2. Wire the primary Action Button to trigger the simulated or live flow.
3. Add animated loading state: An interactive step-by-step progress indicator (e.g., "Step 1: Ingesting..." -> "Step 2: Synthesizing..." -> "Step 3: Verified").
4. Add confetti or a prominent positive success badge when the hero outcome is achieved.
5. Add hotkey listener: Pressing `Space` or `Enter` triggers the primary demo scenario for smooth presenting.

HANDOFF CONTRACT:
- Consumes: B04 UI components
- Produces: Polished interactive client ready for live integration.

ACCEPTANCE CRITERIA:
Done when clicking "Run Demo" produces a captivating, timed progression of states leading to the hero output without stutter or visual glitch.
```
