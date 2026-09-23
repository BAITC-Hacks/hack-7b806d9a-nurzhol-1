const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

// Only the DOM operations used by navigation and error recovery are needed here.
class Element {
  constructor(tagName = "div", id = "") {
    Object.assign(this, { tagName, id, children: [], dataset: {}, hidden: false,
      disabled: false, textContent: "", innerHTML: "", _value: "", _selected: -1,
      classList: { add() {}, remove() {}, toggle() {} } });
  }
  get options() { return this.children; }
  get selectedIndex() { return this._selected; }
  set selectedIndex(index) { this._selected = index; }
  get value() { return this.tagName === "select" ? this.options[this._selected]?.value || "" : this._value; }
  set value(value) {
    if (this.tagName === "select") this._selected = this.options.findIndex(option => option.value === value);
    else this._value = value;
  }
  replaceChildren(...children) { this.children = children; this._selected = children.length ? 0 : -1; }
  append(...children) { this.children.push(...children); if (this._selected < 0) this._selected = 0; }
  querySelector() { return new Element(); }
  querySelectorAll() { return []; }
  addEventListener() {}
  closest() { return null; }
  setAttribute() {}
  scrollIntoView() {}
  click() {}
  remove() {}
}

const forecast = issue => ({
  issue_date: issue, model: { id: "empirical_curve", version: "test-v1" }, source: {},
  forecast_origin_utc: `${issue}T18:00:00+00:00`,
  rows: [1, 2].map(turbine_id => ({ turbine_id, valid_time_utc: `${issue}T19:00:00+00:00`,
    lead_hours: 1, power_normalized: 0.25, wind_speed_ms: 5, temperature_c: 10 }))
});
const savedJob = issue => ({ job_id: "saved", status: "complete", issue_date: issue,
  mode: "deterministic", created_at: "2026-03-01T00:00:00Z", events: [],
  explanation: "Saved forecast", result: forecast(issue) });
const settle = () => new Promise(resolve => setImmediate(resolve));

function harness(dates = ["2026-01-31", "2026-02-01"], options = {}) {
  const elements = new Map();
  const $ = id => {
    if (!elements.has(id)) elements.set(id, new Element(["issue-date", "comparison-issue"].includes(id) ? "select" : "div", id));
    return elements.get(id);
  };
  const requests = [];
  const requestOptions = [];
  const blobs = [];
  const storage = options.storage || new Map();
  const localStorage = {
    getItem: key => { if (options.storageReadFails) throw new Error("Storage blocked"); return storage.get(key) ?? null; },
    setItem: (key, value) => { if (options.storageFails) throw new Error("Storage unavailable"); storage.set(key, value); }
  };
  let responder = async url => { throw new Error(`Unexpected request: ${url}`); };
  const context = vm.createContext({
    document: { getElementById: $, querySelector: () => new Element(), querySelectorAll: () => [], createElement: tag => new Element(tag), body: new Element() },
    URL: { createObjectURL: blob => { blobs.push(blob); return "blob:test"; }, revokeObjectURL() {} },
    Blob, localStorage,
    setTimeout: callback => { queueMicrotask(callback); return 1; }, clearTimeout() {},
    fetch: async () => ({ ok: true, headers: { get: () => "text/csv" }, blob: async () => ({}) })
  });
  const source = fs.readFileSync(path.join(__dirname, "../web/app.js"), "utf8");
  const handlers = source.indexOf('  $("issue-date").addEventListener');
  assert.ok(handlers > 0, "Find the UI initialization boundary");
  // Capture lexical functions in this isolated test context, without production hooks.
  // SVG rendering is outside these regressions; forecast application stays real.
  vm.runInContext(source.slice(0, handlers) + `
    renderForecast = () => {};
    globalThis.app = { state, analytics, applyStatus, setBusy, openSavedJob,
      changeIssue, pollJob, runForecast, loadHistory, askQuestion, download, setRequest: fn => request = fn };
  })();`, context);
  const app = context.app;
  app.setRequest(async (url, options) => { requests.push(url); requestOptions.push(options); return responder(url, options); });
  app.analytics.tab = "validation";
  const status = { available_issues: dates, default_issue_date: dates[0],
    openai_configured: true, model: { id: "empirical_curve", version: "test-v1" },
    data_status: { february_ready: true }, ...options.status };
  app.applyStatus(status);
  return { app, $, context, requests, requestOptions, blobs, storage, status, respond: fn => { responder = fn; } };
}

test("a saved issue missing from the archive keeps navigation and return to an available issue working", async () => {
  const { app, $, respond, requests } = harness();
  respond(async url => {
    if (url === "/api/jobs/saved") return savedJob("2026-02-02");
    if (url === "/api/forecast?issue_date=2026-01-31") return forecast("2026-01-31");
    throw new Error(`Unexpected request: ${url}`);
  });
  await app.openSavedJob("saved");
  assert.equal(app.state.forecast.issue_date, "2026-02-02");
  assert.equal($("issue-date").disabled, false);
  assert.equal($("issue-date").value, "2026-01-31");
  app.changeIssue();
  await settle();
  assert.equal(app.state.snapshot, null);
  assert.equal(app.state.forecast.issue_date, "2026-01-31");
  assert.equal($("snapshot-banner").hidden, true);
  assert.ok(requests.includes("/api/forecast?issue_date=2026-01-31"));
});

test("saved results remain usable with no archive dates and do not offer a dead return action", async () => {
  const { app, $, respond } = harness([]);
  respond(async () => savedJob("2026-02-02"));
  await app.openSavedJob("saved");
  assert.equal(app.state.forecast.issue_date, "2026-02-02");
  assert.equal($("download-button").disabled, false);
  assert.equal($("ask-button").disabled, false);
  assert.equal($("issue-date").disabled, true);
  assert.equal($("snapshot-current").disabled, true);
});

test("opening a saved issue still selects that issue when it exists in the archive", async () => {
  const { app, $, respond } = harness();
  respond(async () => savedJob("2026-02-01"));
  await app.openSavedJob("saved");
  assert.equal($("issue-date").value, "2026-02-01");
  assert.equal($("issue-date").disabled, false);
});

for (const downloadFails of [false, true]) {
  test(`a ${downloadFails ? "failed" : "successful"} monthly download preserves recovery after job polling fails`, async () => {
    const { app, $, context, respond, status } = harness();
    app.state.forecast = forecast("2026-01-31");
    app.state.job = "saved";
    app.setBusy(true);
    respond(async () => { throw new Error("Server disconnected"); });
    await app.pollJob("saved");
    assert.equal($("retry-button").hidden, false);
    if (downloadFails) context.fetch = async () => { throw new Error("CSV unavailable"); };
    await app.download("/api/download-february", "february.csv", $("february-download"));
    assert.equal($("error-banner").hidden, false);
    assert.equal($("retry-button").hidden, false);
    assert.equal(typeof app.state.retry, "function");

    respond(async url => {
      if (url === "/api/jobs/saved") return savedJob("2026-01-31");
      if (url === "/api/status") return status;
      throw new Error(`Unexpected request: ${url}`);
    });
    app.state.retry();
    await settle();
    assert.equal(app.state.busy, false);
    assert.equal(app.state.job, null);
    assert.equal(app.state.forecastJobId, "saved");
    assert.equal($("issue-date").disabled, false);
    assert.equal($("run-button").disabled, false);
  });
}

test("a CSV failure arriving after polling stops cannot replace the job recovery action", async () => {
  const { app, $, context, respond } = harness();
  app.state.job = "saved";
  app.setBusy(true);
  let rejectDownload;
  context.fetch = () => new Promise((_, reject) => { rejectDownload = reject; });
  const downloading = app.download("/api/download-february", "february.csv", $("february-download"));
  respond(async () => { throw new Error("Server disconnected"); });
  await app.pollJob("saved");
  rejectDownload(new Error("CSV unavailable"));
  await downloading;
  assert.equal($("retry-button").hidden, false);
  assert.equal(typeof app.state.retry, "function");
});

const cloudStatus = { execution_mode: "inline", history_storage: "browser", weather_refresh_available: false };

for (const jobStatus of ["complete", "failed"]) {
  test(`inline ${jobStatus} jobs finish without polling and remain in browser history`, async () => {
    const { app, $, respond, requests, requestOptions, storage, status } = harness(undefined, { status: cloudStatus });
    const job = savedJob("2026-01-31");
    if (jobStatus === "failed") Object.assign(job, { status: "failed", result: null, error: "OpenAI unavailable" });
    respond(async url => {
      if (url === "/api/run") return { job_id: job.job_id, job };
      if (url === "/api/status") return status;
      throw new Error(`Unexpected request: ${url}`);
    });
    await app.runForecast();
    assert.equal(app.state.busy, false);
    assert.equal(app.state.job, null);
    assert.ok(!requests.some(url => url.startsWith("/api/jobs")));
    assert.ok(requestOptions[requests.indexOf("/api/run")].timeoutMs > 300000);
    assert.equal(JSON.parse(requestOptions[0].body).refresh, false);
    if (jobStatus === "complete") assert.equal(app.state.forecast.issue_date, "2026-01-31");
    else assert.equal($("error-message").textContent, "OpenAI unavailable");
    await app.loadHistory();
    assert.equal($("history-list").children.length, 1);
    assert.match($("history-status").textContent, /браузер/);
    assert.equal(JSON.parse([...storage.values()][0])[0].status, jobStatus);
  });
}

test("browser history restores a full snapshot after a new page load without server jobs", async () => {
  const first = harness(undefined, { status: cloudStatus });
  first.respond(async url => {
    if (url === "/api/run") return { job_id: "saved", job: savedJob("2026-01-31") };
    if (url === "/api/status") return first.status;
    throw new Error(`Unexpected request: ${url}`);
  });
  await first.app.runForecast();
  const restored = harness(undefined, { status: cloudStatus, storage: first.storage });
  await restored.app.loadHistory();
  await restored.app.openSavedJob("saved");
  assert.equal(restored.app.state.forecast.issue_date, "2026-01-31");
  assert.equal(restored.$("explanation-text").textContent, "Saved forecast");
  assert.equal(restored.$("history-list").children[0].children.length, 3, "A stored result is openable even without a server has_result summary");
  assert.deepEqual(restored.requests, []);
});

test("unavailable browser storage retains results in memory and explains its lifetime", async () => {
  const { app, $, respond, status } = harness(undefined, { status: cloudStatus, storageFails: true });
  respond(async url => {
    if (url === "/api/run") return { job_id: "saved", job: savedJob("2026-01-31") };
    if (url === "/api/status") return status;
    throw new Error(`Unexpected request: ${url}`);
  });
  await app.runForecast();
  await app.loadHistory();
  await app.openSavedJob("saved");
  assert.equal(app.state.forecast.issue_date, "2026-01-31");
  assert.match($("history-status").textContent, /закрытия|перезагрузки/);
});

test("cloud CSV exports the displayed snapshot and every field without a server request", async () => {
  const { app, $, context, blobs } = harness(undefined, { status: cloudStatus });
  app.state.forecast = forecast("2026-01-31");
  app.state.forecast.rows = [{ turbine_id: 1, notes: 'a,"b"\nc', extra: null }, { turbine_id: 2, notes: "", second_row_only: false }];
  app.state.forecastJobId = "expired-server-job";
  $("download-button").disabled = false;
  context.fetch = async () => { throw new Error("Cloud CSV must not refetch a historical snapshot"); };
  await app.download("/api/jobs/expired-server-job/download", "snapshot.csv", $("download-button"));
  assert.equal(blobs.length, 1);
  const bytes = Buffer.from(await blobs[0].arrayBuffer());
  assert.equal(bytes.subarray(0, 3).toString("hex"), "efbbbf");
  assert.equal(bytes.subarray(3).toString(), 'turbine_id,notes,extra,second_row_only\r\n1,"a,""b""\nc",,\r\n2,,,false\r\n');
});

test("cloud questions send displayed snapshot fingerprints instead of ephemeral job IDs", async () => {
  const { app, $, respond, requestOptions } = harness(undefined, { status: cloudStatus });
  app.state.forecast = forecast("2026-01-31");
  app.state.forecast.source.fingerprint = "archive-sha";
  app.state.forecastJobId = "expired-server-job";
  $("question-text").value = "Explain the forecast";
  respond(async () => ({ answer: "Verified forecast", tools: ["get_selected_forecast"] }));
  await app.askQuestion({ preventDefault() {} });
  assert.deepEqual(JSON.parse(requestOptions[0].body), {
    issue_date: "2026-01-31", question: "Explain the forecast", source_fingerprint: "archive-sha", model_version: "test-v1"
  });
  assert.equal($("answer-text").textContent, "Verified forecast");
});

test("cloud status disables NOAA refresh even while idle", () => {
  const { app, $, status } = harness();
  $("refresh-weather").checked = true;
  app.applyStatus({ ...status, ...cloudStatus });
  assert.equal($("refresh-weather").disabled, true);
  assert.equal($("refresh-weather").checked, false);
  assert.match($("refresh-weather").title, /архив|облач/);
});

test("browser history drops the oldest full results after twenty completed runs", async () => {
  const { app, $, respond, storage } = harness(undefined, { status: cloudStatus });
  let sequence = 0;
  respond(async url => {
    if (url !== "/api/run") throw new Error(`Unexpected request: ${url}`);
    const job = { ...savedJob("2026-01-31"), job_id: `job-${++sequence}` };
    return { job_id: job.job_id, job };
  });
  for (let index = 0; index < 21; index++) await app.runForecast();
  await app.loadHistory();
  const jobs = JSON.parse([...storage.values()][0]);
  assert.equal(jobs.length, 20);
  assert.equal(jobs[0].job_id, "job-21");
  assert.equal(jobs.at(-1).job_id, "job-2");
  assert.equal(jobs[0].result.rows.length, 2);
  assert.equal($("history-list").children.length, 20);
});

test("browser history blocked on read still permits an in-memory run and restore", async () => {
  const { app, $, respond } = harness(undefined, { status: cloudStatus, storageReadFails: true, storageFails: true });
  await app.loadHistory();
  assert.equal($("history-list").children.length, 0);
  respond(async url => {
    if (url !== "/api/run") throw new Error(`Unexpected request: ${url}`);
    return { job_id: "saved", job: savedJob("2026-01-31") };
  });
  await app.runForecast();
  await app.openSavedJob("saved");
  assert.equal(app.state.snapshot.job_id, "saved");
  assert.equal(app.state.busy, false);
});

test("a local run still polls the server and questions reference its durable job", async () => {
  const { app, $, respond, requests, requestOptions, status } = harness();
  respond(async url => {
    if (url === "/api/run") return { job_id: "saved" };
    if (url === "/api/jobs/saved") return savedJob("2026-01-31");
    if (url === "/api/status") return status;
    if (url === "/api/ask") return { answer: "Local result", tools: [] };
    throw new Error(`Unexpected request: ${url}`);
  });
  await app.runForecast();
  assert.equal(app.state.busy, false);
  assert.ok(requests.includes("/api/jobs/saved"));
  $("question-text").value = "Explain";
  await app.askQuestion({ preventDefault() {} });
  assert.deepEqual(JSON.parse(requestOptions.at(-1).body), {
    issue_date: "2026-01-31", question: "Explain", job_id: "saved"
  });
});
