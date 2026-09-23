# Prompt B07: Demo Dataset & Seed Agent

- **Time Budget:** 30 Minutes (Run concurrently with B03/B04)
- **Owner:** Member 3 (Biz/Pitch Lead)
- **Cut If Over Time:** Reduce dataset from 10 items to 3 hyper-realistic, dramatic scenarios.

---

```markdown
[PASTE FILLED SHARED CONTEXT BLOCK HERE FROM SHARED_CONTEXT.md]

ROLE: Demo Data Engineer & Domain Expert.
GOAL: Author 3-5 hyper-realistic, domain-authentic data payloads that instantly signal deep industry expertise to judges.

INPUTS:
- `specs/contracts.md`
- `specs/problem_wedge.md`

INSTRUCTIONS:
1. Generate realistic test cases (Avoid generic placeholders like "Test User 1" or "foo@bar.com"). Use realistic enterprise names, UUIDs, realistic timestamps, and convincing technical jargon.
2. Create 3 distinct scenarios:
   - Scenario 1 (The Happy Path): Demonstrates clean speed, high accuracy, and immediate resolution.
   - Scenario 2 (The Hard Attack / Edge Case): Demonstrates the AI's intelligence and reasoning where standard rules-based tools fail.
   - Scenario 3 (The Wild Surprise): Demonstrates a secondary high-value capability (e.g., automated audit log or instant root cause explanation).
3. Save output directly into `data/seeds.json` ready for frontend and backend consumption.
4. Strict Anti-Bloat Directive: Keep `data/seeds.json` as the single canonical dataset. Do NOT generate duplicate dummy JSONs, scratch files, or random sample files.

HANDOFF CONTRACT:
- Produces: `data/seeds.json`
- Consumed by: B05 (Frontend scenarios) and B08 (Mock fallback)

ACCEPTANCE CRITERIA:
Done when every field in `data/seeds.json` matches `specs/contracts.md` and reads like authentic data from an enterprise deployment.
```
