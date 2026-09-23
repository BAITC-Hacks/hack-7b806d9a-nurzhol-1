# 🛡️ Extra Hackathon Essentials

Avoid the classic hackathon traps with these contingency plans, backup demo protocols, and judge engagement tactics.

---

## 1. Backup Demo Video Plan (The "Loom Insurance")

- **Why it matters:** 30% of hackathon live demos fail due to venue Wi-Fi congestion, API rate limits, or audio/HDMI glitches.
- **Protocol:**
  1. **3 Hours Before Deadline:** Member 2 (Frontend) or Member 3 records a clean 60-90 second Loom / OBS screen capture of the complete "Hero Flow".
  2. Use your pre-seeded scenario (`seeds.json`) to guarantee 100% smooth animation and immediate response.
  3. Save the `.mp4` file directly onto the presenter's desktop (`demo_backup.mp4`).
  4. If live latency spikes or an API throws a 429 during presentation, say smoothly:
     > *"Our cloud provider is currently rate-limiting traffic, but here is the exact execution captured 30 minutes ago on our local deployment."*

---

## 2. Live API Failure Fallback Protocol (Offline Hotkey)

- **Rule:** Never deliver a live stage pitch without an instant, seamless offline mode switch.
- **Implementation:**
  1. Build a keybinding (e.g., `Ctrl + Shift + M`) or URL parameter (`?mock=true`) into your frontend.
  2. When active, all network calls to external LLMs/APIs immediately return deterministic canned payloads with an artificial 400-800ms delay to simulate real-time thinking.
  3. No error popups, no red banners—the application continues to look completely live and dynamic.

---

## 3. Rule Compliance & Sponsor Track Strategy

- **Commit Distribution:** Ensure your Git repository shows continuous commits throughout the sprint rather than one giant "Initial commit" 15 minutes before the submission cutoff.
- **Sponsor Prize Requirement:**
  - If competing for a sponsor prize (e.g., Anthropic, Supabase, Pinecone, ElevenLabs):
  - Mention the sponsor technology explicitly in:
    1. Your README badge header and "Built With" section.
    2. Slide 4 (Architecture) of your pitch deck.
    3. The first 45 seconds of your live pitch ("We leveraged X to achieve Y").

---

## 4. The "First 30 Seconds" Rule (Winning the Judges)

Judges evaluate dozens of projects. Win their attention immediately with these rules:

1. **Zero Login Screens:**
   - NEVER start your demo on a `/login`, `/register`, or empty welcome page.
   - Start directly on an active dashboard with rich, realistic data already populated.
2. **Lead with Human Pain, Not Tech Frameworks:**
   - **Bad:** *"We built an AI platform using Next.js 14 and LangChain..."*
   - **Good:** *"Financial fraud investigators spend 30 hours a week sifting through false alerts. We built OmniShield to catch and resolve synthetic identity fraud in under 200 milliseconds."*
3. **Show, Don't Tell:**
   - Trigger the Hero Moment within the first 60 seconds of the presentation. Get the visual reaction first, then explain the architecture that enabled it.

---

## 5. Repository Hygiene & Code Review Quality

Judges often click through the GitHub repository during deliberations. A messy repo drops technical scores:
- **Clean Root:** No leftover debug dumps (`output.json`), test scripts (`temp_test.py`), or scratchpad logs.
- **Single Manifest:** Strictly one `package.json` or single `requirements.txt` (never fragmented `requirements-dev.txt`).
- **Enforce `CLAUDE.md` / `CODEX.md`:** Ensure all AI coding agents follow pre-flight anti-bloat rules.
