# WindOps demo implementation plan

**Goal:** Complete the forecast replay and agent demo within the remaining hackathon window.

**Architecture:** Existing NOAA and frozen empirical models feed a small Python forecast service. A bounded Responses tool loop and deterministic mode share the same functions. A localhost HTTP server serves a static Russian dashboard and job API.

**Spec:** ../specs/2026-09-23-windops-demo-design.md

**Execution:** Parallel bounded tasks, root integrates and reviews. There is no Git repository; preserve existing outputs and write changes in the current authorized project.

## Global constraints

UTC+5, 0..1 power, immutable raw inputs, no fabricated actuals, no historical weather leakage, no secret values in outputs, no cloud deployment. Freeze the validated empirical curve; use OpenAI only for orchestration/explanation. Preserve January audit artifacts.

## Tasks

- [ ] Forecast service + February CLI: agent owns windops/forecast.py, scripts/replay_february.py, tests/test_forecast_service.py. Test time boundaries and rejection before implementation. Root downloads NOAA archive in parallel. Export all29x48x2 predictions and February day-ahead series.
- [ ] Dashboard: agent owns web/index.html, web/app.js, web/style.css. Implement the exact documented HTTP contract, live polling and CSV controls. Review semantics and responsive layout in browser.
- [ ] Root owns windops/agent.py, windops/server.py, run_demo.py, tests/test_agent.py and tests/test_server.py. Use Responses JSON HTTP with standard library, bounded tool count, no arbitrary shell tools, asynchronous local jobs, explicit errors/fallback and no automatic LLM spending.
- [ ] Integration: run archive replay, verify all dates/hours, full test suite, real HTTP/browser flow and live OpenAI test when key available. If key absent, deliver numerical demo and mark live integration unverified.
- [ ] Documentation: launch script, .env.example, updated requirements/README, short demo script and provenance summary.

## Review focus

- Missing or partially downloaded run must not become a complete forecast.
- API key and local files outside web/ cannot be served or logged.
- Refresh and changed weather must invalidate cached predictions.
- Tool arguments cannot change issue date, model, paths or chronology mid-run.
- February output includes correct month filtering without removing March from 48-hour trajectories.

## Decisions

Use the standard library HTTP server and API client to avoid dependency installation and integration overhead. The service binds only localhost. Use gpt-5.4-mini by default with OPENAI_MODEL override, verified in official model documentation. Keep deterministic mode explicitly labelled; it does not satisfy the live LLM demonstration by itself.
