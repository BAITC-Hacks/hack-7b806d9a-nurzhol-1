# 🗺️ Agent Map & Team Run Guide

## 1. Agent Workflow Architecture

```mermaid
flowchart TD
    subgraph StageA["STAGE A: Pre-Hack Strategy & Design"]
        A1["A1: Problem Definition & Wedge Agent"]
        A2["A2: Competitive Landscape & Research Agent"]
        A3["A3: Architecture Spec & Contracts Agent"]
        A4["A4: Visual Design & UX Wireframe Agent"]
        A5["A5: Judge Calibration & Scoring Rubric Agent"]
        
        A1 --> A2
        A1 --> A3
        A3 --> A4
        A1 --> A5
    end

    subgraph Artifacts["Generated Specifications (`specs/`)"]
        ART_SPEC["specs/contracts.md & data-models.md"]
        ART_MKT["specs/market_map.md"]
        ART_UX["specs/wireframes.md & design_tokens.json"]
        ART_RUBRIC["specs/judge_rubric.md"]
    end

    A2 --> ART_MKT
    A3 --> ART_SPEC
    A4 --> ART_UX
    A5 --> ART_RUBRIC

    subgraph StageB["STAGE B: Hackathon Sprint (3-Person Team Execution)"]
        B1["B01: Scaffolding Agent (0-30m)"]
        B2["B02: Core AI Logic Agent (30m-2h)"]
        B3["B03: API & Integration Agent (30m-2h)"]
        B4["B04: Frontend Core View Agent (1h-3h)"]
        B5["B05: UI Polish & Hero Flow Agent (3h-4.5h)"]
        B6["B6: End-to-End Integration Agent (4.5h-5.5h)"]
        B7["B07: Demo Dataset & Seed Agent (Concurrent)"]
        B8["B08: Fallback / Mock Mode Agent (Concurrent)"]
        B9["B09: Pitch Deck & 3-Min Script Agent (5.5h-6.5h)"]
        B10["B10: Judge Q&A Simulator Agent (6.5h-7h)"]
        B11["B11: Final Submission Agent (7h-8h)"]
    end

    ART_SPEC --> B1
    B1 --> B2
    B1 --> B3
    ART_UX --> B4
    B2 & B3 & B4 --> B6
    B4 --> B5
    ART_SPEC --> B7
    B3 --> B8
    B5 & B6 & ART_RUBRIC --> B9
    B9 --> B10
    B10 --> B11
```

---

## 2. 3-Person Team Run Guide

> **Pre-Flight Rule:** All agents (Claude Code, Cursor, Codex, OpenCode) must read and enforce `CLAUDE.md` / `CODEX.md` before generating code or installing packages. Zero extra requirement files, zero scratchpad trash.


| Agent | Stage | Primary Owner | Tool / Runner | Input Artifacts Needed | Output Produced | Cut If Over Time |
|---|---|---|---|---|---|---|
| **A1: Problem & Wedge** | A | All / Lead | Claude / ChatGPT | Raw idea notes | `specs/problem_wedge.md` | Non-negotiable |
| **A2: Research** | A | Member 3 (Biz/Pitch) | Perplexity / Claude | `problem_wedge.md` | `specs/market_map.md` | Deep stats (keep 1 key metric) |
| **A3: Architecture** | A | Member 1 (Backend) | Claude Code / Cursor | `problem_wedge.md` | `specs/contracts.md`, `schema.ts` | Microservices → monolithic script |
| **A4: Visual Design** | A | Member 2 (Frontend) | Claude / v0.dev | `problem_wedge.md` | UI Layouts & mock HTML/CSS | Complex charts/animations |
| **A5: Judge Calibration** | A | Member 3 (Biz/Pitch) | Claude / ChatGPT | Hackathon guidelines | `specs/judge_rubric.md` | Non-negotiable |
| **B01: Scaffolding** | B | Member 1 (Backend) | Terminal / Claude Code | `specs/contracts.md` | Working monorepo / boilerplate | Strict styling lint / CI rules |
| **B02: Core Logic / AI** | B | Member 1 (Backend) | Cursor / Claude Code | `specs/contracts.md` | Pure functions / model inference | Dynamic prompting / fine-tuning |
| **B03: API Endpoints** | B | Member 1 (Backend) | Cursor / Claude Code | `specs/contracts.md` | REST/RPC endpoints + auth stub | Complex auth / multi-tenancy |
| **B04: Frontend Core** | B | Member 2 (Frontend) | Cursor / v0 / Claude Code | `wireframes.md`, `contracts.md` | Working UI views | Sub-pages (keep single dashboard) |
| **B05: UI Polish** | B | Member 2 (Frontend) | Cursor / Claude Code | Frontend views + Seed data | Interactive hero flow + loaders | Dark mode, responsive mobile view |
| **B06: E2E Integration** | B | Member 1 + 2 | Claude Code | B2, B3, B4 outputs | Connected live end-to-end loop | Live LLM generation → canned seed |
| **B07: Demo Seed Data** | B | Member 3 (Biz/Pitch) | Claude / ChatGPT | `specs/contracts.md` | `seeds.json` realistic data | Dynamic generative seeds |
| **B08: Fallback / Mock** | B | Member 1 (Backend) | Claude Code | `specs/contracts.md` | Toggleable offline demo mode | None (Critical for live demo) |
| **B09: Pitch & Script** | B | Member 3 (Biz/Pitch) | Claude / ChatGPT | Live app walkthrough | 3-min script + 5 slide outlines | Extra team slides |
| **B10: Judge Q&A** | B | Member 3 + All | Claude / ChatGPT | `market_map.md`, pitch script | Top 10 hard Q&A cheat sheet | Practice rounds 2-3 |
| **B11: Final Submission**| B | Member 3 (Biz/Pitch) | Browser / Claude | Devpost / GitHub links | Complete README & submission | Video special effects |
