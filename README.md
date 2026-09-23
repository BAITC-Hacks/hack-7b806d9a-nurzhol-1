# Hackathon AI Multi-Agent Playbook

Welcome to your structured Hackathon Multi-Agent workflow suite. This framework decomposes an end-to-end hackathon project into clear, modular prompts designed for a 3-person team using modern AI tools (Claude Code, Cursor, ChatGPT, etc.).

---

## 📁 Repository Structure

```text
Hakcaton/
├── CLAUDE.md                      # Mandatory AI agent codex & anti-bloat rules
├── CODEX.md                       # Codex / Cursor pre-flight hygiene rules
├── README.md                      # This execution overview
├── SHARED_CONTEXT.md              # <-- EDIT THIS FIRST with your idea & stack
├── 00-agent-map-and-guide.md      # Mermaid agent pipeline and team assignment table
├── extra-essentials.md            # Demo fallbacks, video backup plans, and judge advice
│
├── stage-a-strategy/              # Phase 1: Planning & Architecture Specs
│   ├── A1-problem-wedge.md        # Problem statement & 30s hero moment
│   ├── A2-market-research.md      # Competitor landscape & industry metrics
│   ├── A3-architecture-contracts.md # Data models, API schemas & directory layout
│   ├── A4-ux-wireframes.md        # UI blueprint, design tokens & screen layout
│   └── A5-judge-rubric.md         # Rubric reverse-engineering & pitch alignment
│
└── stage-b-sprint/                # Phase 2: 8-Hour Build Sprint Prompts
    ├── B01-scaffolding.md         # Full-stack boilerplate & mock routes (30m)
    ├── B02-core-engine.md         # AI inference loop & core algorithmic logic (90m)
    ├── B03-api-endpoints.md       # API routing, storage & mock handlers (60m)
    ├── B04-frontend-core.md       # Main dashboard layout & components (120m)
    ├── B05-ui-polish.md           # Step-by-step animations & hero moment flow (60m)
    ├── B06-e2e-integration.md     # Full-stack wiring, streaming & error guards (60m)
    ├── B07-demo-seed-data.md      # Hyper-realistic demo scenarios (30m)
    ├── B08-offline-fallback.md    # Zero-dependency local demo mock mode (30m)
    ├── B09-pitch-script.md        # 3-minute pitch script & 5-slide outline (60m)
    ├── B10-judge-qa.md            # Top 10 skeptical judge questions & answers (30m)
    └── B11-final-submission.md    # README.md & Devpost submission generator (60m)
```

---

## 🚀 Quick Start Guide

### Step 1: Fill in `SHARED_CONTEXT.md`
Open `SHARED_CONTEXT.md` and enter:
- Your project name and hackathon track.
- The 1-sentence value proposition.
- The 30-second "Hero Moment" (what will wow the judges).
- The tech stack (frontend, backend, database, LLM APIs).

### Step 2: Run Stage A (Strategy & Specs)
Have your team run prompts `A1` through `A5` to establish solid ground rules before coding.
- **Output:** Stored under a `specs/` directory (`specs/contracts.md`, `specs/wireframes.md`, etc.).
- **Benefit:** Frontend and backend can build simultaneously without waiting on each other.

### Step 3: Run Stage B (Execution Sprint)
Distribute the prompts among your 3 team members according to `00-agent-map-and-guide.md`:
- **Member 1 (Backend/AI Lead):** B01 -> B02 -> B03 -> B08
- **Member 2 (Frontend/UX Lead):** B04 -> B05 -> B06
- **Member 3 (Biz/Pitch/Submission Lead):** B07 -> B09 -> B10 -> B11

Whenever using a Stage B prompt, paste the filled contents of `SHARED_CONTEXT.md` into the indicated header block.
