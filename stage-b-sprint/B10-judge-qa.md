# Prompt B10: Judge Q&A Simulator Agent

- **Time Budget:** 30 Minutes
- **Owner:** Member 3 (Pitch) + All Members
- **Cut If Over Time:** Long answers; condense every answer to 2 hard-hitting sentences.

---

```markdown
[PASTE FILLED SHARED CONTEXT BLOCK HERE FROM SHARED_CONTEXT.md]

ROLE: Skeptical Hackathon Judge & Technical Due Diligence Partner.
GOAL: Prepare the team for the 10 hardest, most skeptical questions judges ask, with concise 2-sentence responses.

INPUTS:
- `specs/problem_wedge.md`
- `specs/contracts.md`
- `pitch/script.md`

INSTRUCTIONS:
1. Identify the 10 most critical attack vectors:
   - "How is this different from a basic ChatGPT wrapper?"
   - "How do you handle hallucination / non-deterministic output?"
   - "What happens when latency spikes to 10 seconds?"
   - "Why wouldn't [OpenAI / Google / Incumbent] just ship this natively?"
   - "How do you make money / what are your unit economics?"
   - 5 domain-specific technical challenges.
2. Formulate 2-sentence structured answers for each:
   - Sentence 1: Direct concession or validation + metric/fact.
   - Sentence 2: The concrete architectural or business reason why our solution wins.
3. Designate which team member answers which category (Biz vs. Backend vs. Frontend).

HANDOFF CONTRACT:
- Produces: `pitch/judge_qa_cheatsheet.md`

ACCEPTANCE CRITERIA:
Done when any team member can immediately answer a skeptical judge question in under 20 seconds without stuttering or contradicting the architecture.
```
