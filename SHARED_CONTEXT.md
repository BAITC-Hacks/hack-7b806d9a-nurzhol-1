# 📋 Hackathon Shared Context Block

> **Instructions:** Fill in the values below as soon as you have chosen your project concept. 
> Whenever you run any **Stage B** prompt (e.g., `B01-scaffolding.md`, `B02-core-engine.md`, etc.), copy the block between the lines and paste it at the top of your prompt.

---

```markdown
### HACKATHON SHARED CONTEXT
- **Project Name:** [e.g., OmniShield AI]
- **Hackathon Track & Theme:** [e.g., Financial Security / Agentic AI Track]
- **One-Sentence Value Prop:** [e.g., Real-time agentic fraud interception that flags and reverses unauthorized AI transactions in under 200ms.]
- **Primary Target User:** [e.g., Fraud Operations Lead at high-frequency fintech startups]
- **The "Hero" Demo Flow (The 30-Second Wow Moment):** [e.g., Live feed shows simulated synthetic identity fraud attack -> Agent intercepts, explains reasoning in graph visualizer, and issues auto-reversal without human intervention.]
- **Tech Stack:**
  - Frontend: [e.g., Next.js 14 (App Router), Tailwind CSS, Lucide React, shadcn/ui]
  - Backend: [e.g., FastAPI (Python 3.11) or Node.js/Express, TypeScript]
  - Database & Storage: [e.g., Supabase / SQLite / In-Memory Mock Store]
  - Models & APIs: [e.g., Claude 3.5 Sonnet via Anthropic SDK, OpenAI API, Tavily Search]
- **Contracts / Schema Reference:** `specs/contracts.md`
- **Fallback / Mock Switch:** Env variable `DEMO_MOCK_MODE=true` (or header `x-demo-mock: true`)
- **Repo Hygiene & Codex:** Strictly enforce `CLAUDE.md` / `CODEX.md` (Zero extra requirements files, zero scratchpad scripts, zero unrequested markdown files)
```
