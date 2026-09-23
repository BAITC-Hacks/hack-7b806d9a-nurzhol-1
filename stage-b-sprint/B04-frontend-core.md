# Prompt B04: Frontend Core View Agent

- **Time Budget:** 120 Minutes
- **Owner:** Member 2 (Frontend)
- **Cut If Over Time:** Complex collapsible sidebars, profile settings, or extra navigation tabs. Keep everything focused on the single hero dashboard view.

---

```markdown
[PASTE FILLED SHARED CONTEXT BLOCK HERE FROM SHARED_CONTEXT.md]

ROLE: Senior UI Engineer & Frontend Specialist.
GOAL: Build the complete single-screen dashboard layout with high-fidelity visual structure matching `specs/wireframes.md`.

INPUTS:
- `specs/wireframes.md`
- `specs/contracts.md`
- `specs/design_tokens.json`

INSTRUCTIONS:
1. Implement the dashboard layout: Header, Control Panel, Execution Stream Visualizer, and Result Card.
2. Use a modern dark-mode aesthetic: Slate-900/Zinc-950 background, high-contrast badges, crisp monospaced logs.
3. Build the state management using React hooks: `idle`, `running`, `success`, `error`.
4. Hardcode mock data from `specs/contracts.md` into initial component state so the UI looks complete and populated immediately upon refresh.
5. Strict Anti-Bloat Directive: Use ONLY established project dependencies. Do NOT add extra UI libraries, multiple icon packs, or duplicate config files.

HANDOFF CONTRACT:
- Consumes: `specs/wireframes.md`, `specs/contracts.md`
- Produces: Completed visual client running on `localhost:3000`.

ACCEPTANCE CRITERIA:
Done when the frontend renders the full layout with mock data, looks beautiful on screen, and contains zero blank cards or raw unformatted JSON dumps.
```
