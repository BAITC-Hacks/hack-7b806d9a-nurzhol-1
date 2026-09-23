"use strict";

(() => {
  const $ = (id) => document.getElementById(id);
  const state = { status: null, forecast: null, turbine: "both", metric: "power", horizon: 48, hour: 0, busy: false, job: null, request: 0, retry: null };
  const HOUR = 3600000;
  const NS = "http://www.w3.org/2000/svg";
  const number = (value, digits = 3) => Number.isFinite(Number(value)) && value !== null && value !== "" ? Number(value).toLocaleString("ru-RU", { minimumFractionDigits: digits, maximumFractionDigits: digits }) : "—";
  const pad = (value) => String(value).padStart(2, "0");
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const parseTime = (value) => {
    if (!value) return NaN;
    return Date.parse(value);
  };
  const parts = (value, local = true) => {
    const time = typeof value === "number" ? value : parseTime(value);
    if (!Number.isFinite(time)) return null;
    const date = new Date(time + (local ? 5 * HOUR : 0));
    return { day: pad(date.getUTCDate()), month: pad(date.getUTCMonth() + 1), year: date.getUTCFullYear(), hour: pad(date.getUTCHours()), minute: pad(date.getUTCMinutes()) };
  };
  const dateTime = (value, local = true, full = false) => {
    const p = parts(value, local);
    return p ? `${p.day}.${p.month}${full ? `.${p.year}` : ""} · ${p.hour}:${p.minute} ${local ? "UTC+5" : "UTC"}` : "—";
  };
  const rowTime = (row) => parseTime(row.valid_time_utc || row.valid_time_local);
  const turbineRows = (turbine) => (state.forecast?.rows || []).filter((row) => String(row.turbine_id) === String(turbine)).sort((a, b) => rowTime(a) - rowTime(b));
  const selectedSeries = () => (state.turbine === "both" ? [1, 2] : [Number(state.turbine)])
    .map((turbine) => ({ turbine, color: turbine === 1 ? "#235be8" : "#b56b16", rows: turbineRows(turbine).slice(0, state.horizon) }))
    .filter((series) => series.rows.length);
  const selectedRows = () => selectedSeries()[0]?.rows || [];
  const mean = (rows, key) => {
    const values = rows.map((row) => row[key]).filter((value) => value !== null && value !== "" && Number.isFinite(Number(value))).map(Number);
    return values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : null;
  };
  const peak = (rows) => rows.length ? Math.max(...rows.map((row) => Number(row.power_normalized))) : null;
  const setText = (id, value) => { $(id).textContent = value; };
  const renderTurbineValues = (id, series, valueFor, digits = 3) => {
    if (!series.length) { setText(id, "—"); return; }
    $(id).replaceChildren(...series.map((item) => {
      const value = document.createElement("span");
      value.className = "turbine-value";
      value.dataset.turbineId = String(item.turbine);
      const label = document.createElement("span");
      label.className = "turbine-label";
      label.textContent = `Т${item.turbine}`;
      const amount = document.createElement("span");
      amount.className = "turbine-number";
      amount.textContent = number(valueFor(item), digits);
      value.append(label, document.createTextNode(" "), amount);
      return value;
    }));
  };
  const announce = (message) => setText("live-status", message);
  const svgNode = (name, attrs = {}, text) => {
    const node = document.createElementNS(NS, name);
    Object.entries(attrs).forEach(([key, value]) => node.setAttribute(key, String(value)));
    if (text !== undefined) node.textContent = text;
    return node;
  };

  async function request(url, options = {}) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 45000);
    let response;
    try { response = await fetch(url, { ...options, signal: controller.signal, headers: { Accept: "application/json", ...options.headers } }); }
    catch (error) {
      throw new Error(error.name === "AbortError" ? "Сервер не ответил за 45 секунд. Проверьте локальный сервер и повторите запрос." : "Нет соединения с локальным сервером. Убедитесь, что WindOps запущен, и повторите запрос.");
    } finally { clearTimeout(timer); }
    let data;
    try { data = await response.json(); } catch { throw new Error(`Сервер вернул неожиданный ответ (${response.status}). Повторите запрос.`); }
    if (!response.ok || (data.error && !["running", "complete", "failed"].includes(data.status))) {
      const error = data.error;
      const message = typeof error === "string" ? error : error?.message || data.message || `Ошибка сервера (${response.status})`;
      throw new Error(message);
    }
    return data;
  }

  function showError(message, retry = null) {
    setText("error-message", message);
    $("error-banner").hidden = false;
    $("retry-button").hidden = !retry;
    state.retry = retry;
    announce(message);
  }

  function clearError() {
    $("error-banner").hidden = true;
    state.retry = null;
  }

  function setBusy(busy) {
    state.busy = busy;
    $("issue-date").disabled = busy || !$("issue-date").value;
    $("refresh-weather").disabled = busy || !state.status?.openai_configured;
    $("run-button").disabled = busy || !state.status?.openai_configured || !$("issue-date").value;
    $("run-button").querySelector("span").textContent = busy ? "Идёт расчёт…" : "Рассчитать прогноз";
    $("download-button").disabled = busy || !state.forecast;
    updateIssueNavigation();
  }

  function updateIssueNavigation() {
    const select = $("issue-date");
    $("prev-issue").disabled = state.busy || select.selectedIndex <= 0 || !select.value;
    $("next-issue").disabled = state.busy || select.selectedIndex < 0 || select.selectedIndex >= select.options.length - 1 || !select.value;
  }

  function chartLoading(message = "Загружаем почасовой прогноз", empty = false) {
    $("forecast").setAttribute("aria-busy", String(!empty));
    $("forecast-chart").setAttribute("hidden", "");
    $("chart-placeholder").hidden = false;
    $("chart-placeholder").classList.toggle("is-empty", empty);
    setText("chart-placeholder-text", message);
    $("chart-tooltip").hidden = true;
  }

  function resetForecast() {
    state.forecast = null;
    $("download-button").disabled = true;
    $("baseline").hidden = true;
    ["origin-value", "weather-run-value", "weather-available-value"].forEach((id) => setText(id, "—"));
    setText("forecast-range", "Прогноз ещё не загружен");
    $("summary-body").replaceChildren(...[24, 48].map((hours) => summaryRow(hours, [])));
    $("hourly-table-body").replaceChildren();
    ["kpi-power", "kpi-peak", "kpi-wind", "inspector-time", "inspector-power", "inspector-wind", "inspector-temperature"].forEach((id) => setText(id, "—"));
    setText("kpi-context", "Выберите выпуск для просмотра прогноза");
    state.hour = 0;
  }

  function applyStatus(status) {
    state.status = status;
    const dates = (status.available_issues || []).map((entry) => typeof entry === "string" ? entry : entry.issue_date).filter(Boolean).sort();
    const selected = $("issue-date").value;
    $("issue-date").replaceChildren();
    for (const date of dates) {
      const option = document.createElement("option");
      option.value = date;
      const dateValue = new Date(`${date}T12:00:00Z`);
      option.textContent = dateValue.toLocaleDateString("ru-RU", { day: "numeric", month: "long", year: "numeric", timeZone: "UTC" });
      $("issue-date").append(option);
    }
    if (!dates.length) {
      const option = document.createElement("option");
      option.value = "";
      option.textContent = "Нет доступных выпусков";
      $("issue-date").append(option);
    } else {
      $("issue-date").value = dates.includes(selected) ? selected : dates.includes(status.default_issue_date) ? status.default_issue_date : dates[0];
    }
    setText("run-help", status.openai_configured
      ? "Расчёт с AI-анализом: модель прогнозирует мощность, OpenAI объясняет результат."
      : "AI-анализ недоступен: API-ключ OpenAI не настроен на сервере. Просмотр и скачивание архивных прогнозов доступны.");
    const data = status.data_status || {};
    const available = data.available_issue_count ?? dates.length;
    const expected = data.expected_issue_count ?? 29;
    setText("coverage-text", `Архив погоды · ${available} / ${expected} выпусков`);
    $("coverage").querySelector(".status-dot").className = `status-dot${data.february_ready ? "" : " partial"}`;
    $("coverage").title = data.message || "Готовность архивных данных NOAA";
    $("february-download").disabled = !data.february_ready;
    setText("february-help", data.february_ready ? "672 часа на турбину · 1 344 строки" : data.message || "Полный экспорт станет доступен после загрузки архива погоды.");
    setBusy(state.busy);
    if (status.model) renderModel(status.model);
  }

  function renderModel(model) {
    if (typeof model === "string") { setText("model-value", model); return; }
    setText("model-value", model.label === "Frozen empirical power curve" ? "Фиксированная эмпирическая кривая" : model.label || "Фиксированная эмпирическая кривая");
    setText("footer-model", `NOAA GFS · ${model.id === "empirical_curve" ? "ЭМПИРИЧЕСКАЯ КРИВАЯ" : model.label || model.id} · UTC+5`);
    const cutoff = model.training_cutoff || model.training_cutoff_utc;
    setText("model-cutoff", cutoff ? `Граница обучения: ${String(cutoff).slice(0, 10)}. Модель фиксирована для всего replay.` : "Модель фиксирована для всего replay и не дообучается на февральских данных.");
  }

  async function loadForecast() {
    if (!$("issue-date").value) return;
    const id = ++state.request;
    const issue = $("issue-date").value;
    clearError();
    resetForecast();
    chartLoading();
    try {
      const forecast = await request(`/api/forecast?issue_date=${encodeURIComponent(issue)}`);
      if (id !== state.request) return;
      await applyForecast(forecast);
      if (id !== state.request) return;
      announce(`Прогноз выпуска ${issue} загружен.`);
    } catch (error) {
      if (id !== state.request) return;
      chartLoading("Прогноз недоступен. Проверьте данные и повторите загрузку.", true);
      showError(error.message, loadForecast);
    }
  }

  async function applyForecast(forecast) {
    if (!Array.isArray(forecast.rows) || !forecast.rows.length) throw new Error("Сервер вернул прогноз без почасовых значений.");
    if (forecast.model?.version !== state.status?.model?.version) {
      const requestId = state.request;
      const status = await request("/api/status");
      if (requestId !== state.request) return;
      applyStatus(status);
    }
    state.forecast = forecast;
    $("forecast").setAttribute("aria-busy", "false");
    $("download-button").disabled = state.busy;
    const ids = [...new Set(forecast.rows.map((row) => Number(row.turbine_id)))];
    if (state.turbine !== "both" && !ids.includes(Number(state.turbine))) state.turbine = ids[0];
    document.querySelectorAll("[data-turbine]").forEach((button) => { button.disabled = button.dataset.turbine === "both" ? ids.length < 2 : !ids.includes(Number(button.dataset.turbine)); });
    const times = forecast.rows.map(rowTime).filter(Number.isFinite);
    const first = parts(Math.min(...times));
    const last = parts(Math.max(...times));
    setText("forecast-range", first && last ? `${first.day}.${first.month}.${first.year} ${first.hour}:00 — ${last.day}.${last.month}.${last.year} ${last.hour}:00 · UTC+5` : "Все значения в UTC+5");
    setText("origin-value", dateTime(forecast.forecast_origin_utc || forecast.forecast_origin_local, true, true));
    const source = forecast.source || {};
    setText("weather-run-value", dateTime(source.run_time_utc, false));
    setText("weather-available-value", dateTime(source.available_at_utc, false));
    setText("provider-value", source.provider || "NOAA GFS");
    $("provider-value").title = source.fingerprint ? `Отпечаток данных: ${source.fingerprint}` : "Архивные метеоданные";
    const hasSpillover = forecast.rows.some((row) => row.is_february_target === false);
    setText("chart-end-note", hasSpillover ? "Включены часы марта · полный горизонт 48 ч" : "Почасовой прогноз · фактических данных февраля нет");
    if (forecast.model) renderModel(forecast.model);
    renderForecast();
  }

  function summaryRow(hours, rows, label = null) {
    const selected = rows.slice(0, hours);
    const row = document.createElement("tr");
    const heading = document.createElement("th");
    heading.scope = "row";
    heading.textContent = label || (hours === 24 ? "Первые 24 ч" : "Все 48 ч");
    row.append(heading);
    const finiteValues = (key) => selected.map((item) => Number(item[key])).filter(Number.isFinite);
    const power = finiteValues("power_normalized");
    const wind = finiteValues("wind_speed_ms");
    const mean = (values) => values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : null;
    [number(mean(power)), number(power.length ? Math.max(...power) : null), number(mean(wind), 1)].forEach((value) => {
      const cell = document.createElement("td");
      cell.textContent = value;
      row.append(cell);
    });
    return row;
  }

  function renderForecast() {
    document.querySelectorAll("[data-turbine]").forEach((button) => {
      const active = button.dataset.turbine === String(state.turbine);
      button.classList.toggle("is-active", active);
      button.setAttribute("aria-pressed", String(active));
    });
    document.querySelectorAll("[data-metric]").forEach((button) => {
      const active = button.dataset.metric === state.metric;
      button.classList.toggle("is-active", active);
      button.setAttribute("aria-pressed", String(active));
    });
    document.querySelectorAll("[data-horizon]").forEach((button) => {
      const active = Number(button.dataset.horizon) === state.horizon;
      button.classList.toggle("is-active", active);
      button.setAttribute("aria-pressed", String(active));
    });
    const both = state.turbine === "both";
    const series = selectedSeries();
    const rows = selectedRows();
    setText("forecast-title", `Прогноз на ${state.horizon} ${state.horizon === 24 ? "часа" : "часов"}`);
    setText("summary-title", both ? `Сравнение турбин · ${state.horizon} ч` : `Турбина ${state.turbine} · 24 и 48 часов`);
    const firstHeading = $("summary-body").closest("table")?.querySelector("thead th");
    if (firstHeading) firstHeading.textContent = both ? "Турбина" : "Горизонт";
    $("summary-body").replaceChildren(...(both
      ? series.map((item) => summaryRow(state.horizon, item.rows, `Турбина ${item.turbine}`))
      : [24, 48].map((hours) => summaryRow(hours, turbineRows(state.turbine)))));
    if (rows.length) {
      const first = parts(rowTime(rows[0]));
      const last = parts(rowTime(rows.at(-1)));
      setText("forecast-range", `${first.day}.${first.month}.${first.year} ${first.hour}:00 — ${last.day}.${last.month}.${last.year} ${last.hour}:00 · UTC+5`);
      const spillover = rows.some((row) => row.is_february_target === false);
      setText("chart-end-note", spillover ? `Показаны часы марта · горизонт ${state.horizon} ч` : "Почасовой прогноз · фактических данных февраля нет");
    }
    setText("kpi-context", both ? `Т1 / Т2 · первые ${state.horizon} ч · значения по каждой турбине` : `Турбина ${state.turbine} · первые ${state.horizon} ч`);
    renderTurbineValues("kpi-power", series, (item) => mean(item.rows, "power_normalized"));
    renderTurbineValues("kpi-peak", series, (item) => peak(item.rows));
    renderTurbineValues("kpi-wind", series, (item) => mean(item.rows, "wind_speed_ms"), 1);
    const metrics = state.status?.model?.version === state.forecast?.model?.version ? state.status?.baseline_metrics : [];
    const metricFor = (turbine) => Array.isArray(metrics) ? metrics.find((item) => String(item.turbine_id) === String(turbine)) : metrics?.[turbine] || metrics?.[`turbine_${turbine}`];
    const baselines = (both ? [1, 2] : [state.turbine]).map((turbine) => ({ turbine, metric: metricFor(turbine) }));
    $("baseline").hidden = !baselines.some((item) => item.metric?.rmse !== null && Number.isFinite(Number(item.metric?.rmse)));
    setText("rmse-value", baselines.map((item) => `${both ? `Т${item.turbine} ` : ""}${number(item.metric?.rmse)}`).join(" / "));
    if (state.status?.evaluation) {
      setText("validation-label", state.status.evaluation.label);
      setText("validation-description", state.status.evaluation.description);
    }
    renderHourlyTable(series);
    renderChart();
  }

  function renderHourlyTable(series) {
    const both = state.turbine === "both";
    setText("hourly-table-caption", `${both ? "Турбины 1 и 2" : `Турбина ${state.turbine}`} · ${state.horizon} ч · UTC+5`);
    const heading = document.createElement("tr");
    const labels = both ? ["Время · UTC+5", "Т1 · мощность 0–1", "Т2 · мощность 0–1", "Т1 · ветер м/с", "Т2 · ветер м/с", "Т1 · °C", "Т2 · °C"]
      : ["Время · UTC+5", "Мощность · 0–1", "Ветер · м/с", "Температура · °C"];
    labels.forEach((label) => { const cell = document.createElement("th"); cell.scope = "col"; cell.textContent = label; heading.append(cell); });
    $("hourly-table-head").replaceChildren(heading);
    const rows = series[0]?.rows || [];
    const second = new Map((series[1]?.rows || []).map((row) => [rowTime(row), row]));
    $("hourly-table-body").replaceChildren(...rows.map((row) => {
      const tr = document.createElement("tr");
      const other = second.get(rowTime(row));
      const values = both ? [number(row.power_normalized), number(other?.power_normalized), number(row.wind_speed_ms, 2), number(other?.wind_speed_ms, 2), number(row.temperature_c, 1), number(other?.temperature_c, 1)]
        : [number(row.power_normalized), number(row.wind_speed_ms, 2), number(row.temperature_c, 1)];
      [dateTime(rowTime(row), true, true).replace(" UTC+5", ""), ...values].forEach((value, index) => {
        const cell = document.createElement(index === 0 ? "th" : "td");
        if (index === 0) cell.scope = "row";
        cell.textContent = value;
        tr.append(cell);
      });
      return tr;
    }));
  }

  let updateCrosshair = () => {};

  function inspectHour(hour) {
    const series = selectedSeries();
    const rows = series[0]?.rows || [];
    if (!rows.length) return;
    state.hour = Math.max(0, Math.min(rows.length - 1, Math.round(Number(hour) || 0)));
    const time = rowTime(rows[state.hour]);
    setText("inspector-time", dateTime(time, true, true));
    const valueAtHour = (item, key) => item.rows.find((entry) => rowTime(entry) === time)?.[key];
    renderTurbineValues("inspector-power", series, (item) => valueAtHour(item, "power_normalized"), 3);
    renderTurbineValues("inspector-wind", series, (item) => valueAtHour(item, "wind_speed_ms"), 2);
    renderTurbineValues("inspector-temperature", series, (item) => valueAtHour(item, "temperature_c"), 1);
    updateCrosshair();
  }

  function renderChart() {
    if (!state.forecast) return;
    const series = selectedSeries();
    const rows = series[0]?.rows || [];
    if (!rows.length) { chartLoading("Нет почасовых значений для выбранной турбины.", true); return; }
    const chart = $("forecast-chart");
    const container = $("chart-container");
    const width = Math.max(240, container.clientWidth);
    const height = Math.max(220, container.clientHeight);
    const compact = width < 650;
    const padding = { left: compact ? 35 : 46, right: compact ? 13 : 24, top: 35, bottom: 45 };
    const plotWidth = width - padding.left - padding.right;
    const plotHeight = height - padding.top - padding.bottom;
    const power = state.metric === "power";
    const key = power ? "power_normalized" : "wind_speed_ms";
    const values = series.flatMap((item) => item.rows.map((row) => Number(row[key]))).filter(Number.isFinite);
    const maxY = power ? 1 : Math.max(4, Math.ceil(Math.max(...values) / 4) * 4);
    const x = (hour) => padding.left + hour / state.horizon * plotWidth;
    const y = (value) => padding.top + plotHeight - value / maxY * plotHeight;
    const time0 = rowTime(rows[0]);
    const style = getComputedStyle(document.documentElement);
    const gridColor = style.getPropertyValue("--chart-grid").trim() || "#e7ebf2";
    const labelColor = style.getPropertyValue("--chart-label").trim() || "#6d7890";
    const dayFill = style.getPropertyValue("--chart-day-fill").trim() || style.getPropertyValue("--chart-day").trim() || "#f6f8fc";
    chart.setAttribute("viewBox", `0 0 ${width} ${height}`);
    chart.replaceChildren();
    const title = `${state.turbine === "both" ? "Турбины 1 и 2" : `Турбина ${state.turbine}`}: ${power ? "нормированная мощность" : "скорость ветра"}, ${state.horizon} часов`;
    chart.append(svgNode("title", { id: "chart-title" }, title));
    chart.append(svgNode("desc", { id: "chart-description" }, `${series.map((item) => item.turbine === 1 ? "Синяя сплошная линия — турбина 1" : "Оранжевая пунктирная линия — турбина 2").join(". ")}. Значения турбин показаны отдельно. Выберите час на графике или откройте таблицу почасовых значений.`));
    if (state.horizon === 48) chart.append(svgNode("rect", { x: x(24), y: padding.top, width: plotWidth / 2, height: plotHeight, fill: dayFill }));
    for (let step = 0; step <= 4; step++) {
      const value = maxY / 4 * step;
      chart.append(svgNode("line", { x1: padding.left, x2: width - padding.right, y1: y(value), y2: y(value), stroke: gridColor, "stroke-width": 1 }));
      chart.append(svgNode("text", { x: padding.left - 9, y: y(value) + 3, "text-anchor": "end", fill: labelColor, "font-size": compact ? 9 : 10, "font-family": "monospace" }, number(value, power ? 2 : 0)));
    }
    const tickStep = state.horizon / (compact ? 4 : 8);
    for (let hour = 0; hour <= state.horizon; hour += tickStep) {
      const p = parts(time0 + hour * HOUR);
      chart.append(svgNode("line", { x1: x(hour), x2: x(hour), y1: y(0), y2: y(0) + 5, stroke: gridColor, "stroke-width": 1 }));
      chart.append(svgNode("text", { x: x(hour), y: y(0) + 21, "text-anchor": hour === 0 ? "start" : hour === state.horizon ? "end" : "middle", fill: labelColor, "font-size": compact ? 9 : 10, "font-family": "monospace" }, `${p.hour}:00`));
    }
    if (state.horizon === 48) chart.append(svgNode("line", { x1: x(24), x2: x(24), y1: padding.top, y2: y(0), stroke: labelColor, opacity: .45, "stroke-width": 1, "stroke-dasharray": "3 4" }));
    (state.horizon === 48 ? [0, 24] : [0]).forEach((hour, index) => {
      const p = parts(time0 + hour * HOUR);
      const label = compact ? `${p.day}.${p.month} / ${index + 1}-й день` : `${p.day}.${p.month}.${p.year} / ${index + 1}-й день`;
      chart.append(svgNode("text", { x: x(hour) + 4, y: 16, fill: labelColor, "font-size": compact ? 9 : 10, "font-family": "monospace" }, label));
    });
    series.forEach((item) => {
      const points = item.rows.map((row) => [x((rowTime(row) - time0) / HOUR + .5), y(Number(row[key]))]).filter((point) => point.every(Number.isFinite));
      const path = points.map((point, index) => `${index ? "L" : "M"}${point[0].toFixed(2)},${point[1].toFixed(2)}`).join(" ");
      if (!points.length) return;
      if (series.length === 1) chart.append(svgNode("path", { d: `${path} L${points.at(-1)[0]},${y(0)} L${points[0][0]},${y(0)} Z`, fill: item.color, opacity: .06 }));
      chart.append(svgNode("path", { d: path, fill: "none", stroke: item.color, "stroke-width": compact ? 2.1 : 2.5, "stroke-linejoin": "round", "stroke-linecap": "round", ...(item.turbine === 2 ? { "stroke-dasharray": "6 3" } : {}) }));
      if (!compact) points.forEach(([cx, cy]) => chart.append(svgNode("circle", { cx, cy, r: 1.7, fill: "#fff", stroke: item.color, "stroke-width": 1.2 })));
    });
    const hoverLine = svgNode("line", { x1: 0, x2: 0, y1: padding.top, y2: y(0), stroke: labelColor, opacity: .55, "stroke-dasharray": "3 4", "pointer-events": "none" });
    chart.append(hoverLine);
    const markers = series.map((item) => {
      const marker = svgNode("circle", { cx: 0, cy: 0, r: compact ? 3.4 : 4, fill: "#fff", stroke: item.color, "stroke-width": 2, "pointer-events": "none" });
      chart.append(marker);
      return marker;
    });
    updateCrosshair = () => {
      const time = rowTime(rows[state.hour]);
      const position = x((time - time0) / HOUR + .5);
      hoverLine.setAttribute("x1", position);
      hoverLine.setAttribute("x2", position);
      series.forEach((item, index) => {
        const row = item.rows.find((entry) => rowTime(entry) === time);
        markers[index].setAttribute("cx", position);
        markers[index].setAttribute("cy", y(Number(row?.[key])));
      });
    };
    const interaction = svgNode("rect", { x: padding.left, y: padding.top, width: plotWidth, height: plotHeight, fill: "transparent" });
    const tooltip = $("chart-tooltip");
    const choosePointerHour = (event) => {
      const bounds = chart.getBoundingClientRect();
      const mouseX = (event.clientX - bounds.left) / bounds.width * width;
      inspectHour(Math.floor((mouseX - padding.left) / plotWidth * state.horizon));
      return mouseX;
    };
    interaction.addEventListener("pointermove", (event) => {
      const mouseX = choosePointerHour(event);
      const time = rowTime(rows[state.hour]);
      tooltip.replaceChildren();
      const timeLabel = document.createElement("div");
      timeLabel.textContent = dateTime(time);
      tooltip.append(timeLabel);
      series.forEach((item) => {
        const row = item.rows.find((entry) => rowTime(entry) === time);
        const value = document.createElement("div");
        value.textContent = `Т${item.turbine}: ${number(row?.[key], power ? 3 : 2)} ${power ? "/ 1" : "м/с"}`;
        tooltip.append(value);
      });
      tooltip.hidden = false;
      tooltip.style.left = `${Math.min(container.clientWidth - tooltip.offsetWidth - 6, Math.max(5, mouseX / width * container.clientWidth + 12))}px`;
      tooltip.style.top = "27px";
    });
    interaction.addEventListener("click", choosePointerHour);
    interaction.addEventListener("pointerleave", () => { tooltip.hidden = true; });
    chart.append(interaction);
    const legend = series.map((item) => {
      const label = document.createElement("span");
      label.className = "legend-item";
      const swatch = document.createElement("i");
      swatch.className = "legend-swatch";
      swatch.style.color = item.color;
      swatch.style.borderTop = `3px ${item.turbine === 2 ? "dashed" : "solid"} ${item.color}`;
      swatch.style.width = "20px";
      swatch.style.display = "inline-block";
      swatch.setAttribute("aria-hidden", "true");
      label.append(swatch, document.createTextNode(`Турбина ${item.turbine}`));
      return label;
    });
    $("chart-legend").replaceChildren(...legend);
    $("series-swatch").style.background = series.length === 1 ? series[0].color : "#657089";
    setText("series-label", power ? "Мощность каждой турбины · 0–1" : "Скорость ветра · м/с");
    $("chart-placeholder").hidden = true;
    chart.removeAttribute("hidden");
    tooltip.hidden = true;
    inspectHour(state.hour);
  }

  function jobStatus(status, text) {
    $("job-status").className = `job-status${status ? ` is-${status}` : ""}`;
    setText("job-status", text);
  }

  function renderEvents(events = []) {
    const latest = new Map();
    const stages = ["get_weather", "validate_weather", "predict_power", "analyze_forecast"];
    events.forEach((event) => {
      const index = stages.indexOf(event.tool);
      if (index >= 0 && ["running", "started"].includes(event.status)) {
        stages.slice(index + 1).forEach((stage) => latest.delete(stage));
      }
      latest.set(event.tool, event);
    });
    const labels = { pending: "Ожидает", running: "Выполняется", complete: "Готово", failed: "Ошибка" };
    $("agent-steps").querySelectorAll("[data-step]").forEach((step) => {
      if (!step.dataset.pendingDetail) step.dataset.pendingDetail = step.querySelector(".step-detail").textContent;
      const event = latest.get(step.dataset.step);
      const status = !event ? "pending" : event.status === "complete" ? "complete"
        : ["failed", "error"].includes(event.status) ? "failed"
          : ["running", "started"].includes(event.status) ? "running" : "pending";
      step.dataset.state = status;
      ["running", "complete", "failed"].forEach((value) => step.classList.toggle(`is-${value}`, status === value));
      step.querySelector(".step-state").textContent = labels[status];
      step.querySelector(".step-detail").textContent = event?.message || step.dataset.pendingDetail;
    });
    const list = $("event-list");
    const followLatest = list.scrollHeight - list.scrollTop - list.clientHeight < 35;
    list.replaceChildren(...events.map((event) => {
      const item = document.createElement("li");
      const dot = document.createElement("span");
      dot.className = `event-dot ${event.status === "failed" || event.status === "error" ? "failed" : event.status === "running" || event.status === "started" ? "running" : ""}`;
      dot.setAttribute("aria-hidden", "true");
      const time = document.createElement("span");
      time.className = "event-time";
      const p = parts(event.time);
      time.textContent = p ? `${p.hour}:${p.minute}` : "—";
      time.title = p ? dateTime(event.time, true, true) : "Время события не указано";
      const detail = document.createElement("div");
      const tool = document.createElement("p");
      tool.className = "event-tool";
      tool.textContent = event.tool || "Событие";
      const message = document.createElement("p");
      message.className = "event-message";
      message.textContent = event.message || event.status || "";
      detail.append(tool, message);
      item.append(dot, time, detail);
      return item;
    }));
    if (followLatest) list.scrollTop = list.scrollHeight;
  }

  async function pollJob(jobId) {
    let failures = 0;
    while (state.job === jobId) {
      let job;
      try { job = await request(`/api/jobs/${encodeURIComponent(jobId)}`); failures = 0; }
      catch (error) {
        failures++;
        if (failures >= 3) {
          jobStatus("failed", "Нет связи с сервером");
          showError(`${error.message} Расчёт мог продолжиться на сервере.`, () => { clearError(); jobStatus("running", "Проверяем расчёт"); pollJob(jobId); });
          return;
        }
        await sleep(1800);
        continue;
      }
      renderEvents(job.events);
      if (job.explanation) {
        setText("explanation-label", "AI-анализ прогноза");
        setText("explanation-text", job.explanation);
        $("explanation").hidden = false;
      }
      if (job.status === "complete") {
        try {
          applyStatus(await request("/api/status"));
          if (job.result) await applyForecast(job.result);
          else await loadForecast();
          jobStatus("complete", "Расчёт завершён");
          setText("agent-intro", "Агент OpenAI завершил расчёт и анализ прогноза. Журнал событий — UTC+5.");
          announce("Расчёт завершён. Прогноз обновлён.");
        } catch (error) { showError(error.message, loadForecast); }
        state.job = null;
        setBusy(false);
        return;
      }
      if (job.status === "failed") {
        jobStatus("failed", "Ошибка расчёта");
        setText("agent-intro", "Расчёт остановлен. Подробности сохранены в журнале инструментов.");
        showError(typeof job.error === "string" ? job.error : job.error?.message || "Не удалось завершить расчёт. Проверьте журнал и повторите запуск.");
        state.job = null;
        setBusy(false);
        return;
      }
      await sleep(1200);
    }
  }

  async function runForecast() {
    if (state.busy || !state.status?.openai_configured || !$("issue-date").value) return;
    state.request++;
    clearError();
    setBusy(true);
    renderEvents();
    $("explanation").hidden = true;
    jobStatus("running", "Выполняется");
    setText("agent-intro", "Агент OpenAI проверяет погоду, вызывает модель и анализирует результат. Журнал — UTC+5.");
    announce("Расчёт запущен.");
    try {
      const job = await request("/api/run", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ issue_date: $("issue-date").value, mode: "openai", refresh: $("refresh-weather").checked }) });
      if (!job.job_id) throw new Error("Сервер не вернул идентификатор расчёта. Повторите запуск.");
      state.job = job.job_id;
      await pollJob(job.job_id);
    } catch (error) {
      state.job = null;
      setBusy(false);
      jobStatus("failed", "Ошибка запуска");
      showError(error.message);
    }
  }

  async function download(url, filename, button) {
    if (button.disabled) return;
    clearError();
    button.disabled = true;
    const oldLabel = button.innerHTML;
    button.textContent = "Готовим CSV…";
    try {
      const response = await fetch(url, { headers: { Accept: "text/csv" } });
      if (!response.ok || (response.headers.get("Content-Type") || "").includes("application/json")) {
        let data = {};
        try { data = await response.json(); } catch { /* Error body may not be JSON. */ }
        throw new Error(typeof data.error === "string" ? data.error : data.error?.message || `Не удалось скачать CSV (${response.status}).`);
      }
      const blob = await response.blob();
      const blobURL = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = blobURL;
      anchor.download = filename;
      document.body.append(anchor);
      anchor.click();
      anchor.remove();
      setTimeout(() => URL.revokeObjectURL(blobURL), 1000);
      announce("CSV подготовлен и передан браузеру для скачивания.");
    } catch (error) { showError(error.message || "Не удалось скачать CSV. Проверьте соединение и повторите попытку."); }
    finally { button.innerHTML = oldLabel; button.disabled = button.id === "download-button" ? state.busy || !state.forecast : !state.status?.data_status?.february_ready; }
  }

  async function boot() {
    clearError();
    chartLoading();
    try {
      applyStatus(await request("/api/status"));
      if ($("issue-date").value) await loadForecast();
      else { chartLoading("Нет доступных выпусков. Загрузите архив погоды и повторите проверку.", true); showError("Сервер пока не располагает данными для прогноза.", boot); }
    } catch (error) {
      setText("coverage-text", "Сервер недоступен");
      chartLoading("Не удалось подключиться к локальному серверу.", true);
      showError(error.message, boot);
    }
  }

  function changeIssue() {
    renderEvents();
    $("explanation").hidden = true;
    jobStatus("", "Готов к запуску");
    setText("agent-intro", "Запустите расчёт, чтобы увидеть реальные вызовы инструментов и результат проверки данных.");
    updateIssueNavigation();
    loadForecast();
  }
  $("issue-date").addEventListener("change", changeIssue);
  $("prev-issue").addEventListener("click", () => {
    if (state.busy || $("issue-date").selectedIndex <= 0) return;
    $("issue-date").selectedIndex--;
    changeIssue();
  });
  $("next-issue").addEventListener("click", () => {
    const select = $("issue-date");
    if (state.busy || select.selectedIndex >= select.options.length - 1) return;
    select.selectedIndex++;
    changeIssue();
  });
  $("run-button").addEventListener("click", runForecast);
  $("retry-button").addEventListener("click", () => state.retry?.());
  $("download-button").addEventListener("click", () => download(`/api/download?issue_date=${encodeURIComponent($("issue-date").value)}`, `windops_${$("issue-date").value}_48h.csv`, $("download-button")));
  $("february-download").addEventListener("click", () => download("/api/download-february", "windops_february_2026_day_ahead.csv", $("february-download")));
  $("table-toggle").addEventListener("click", () => {
    const open = $("hourly-table").hidden;
    $("hourly-table").hidden = !open;
    $("table-toggle").setAttribute("aria-expanded", String(open));
    $("table-toggle").textContent = open ? "Скрыть почасовые значения ↑" : "Показать значения по часам ↗";
  });
  document.querySelectorAll("[data-turbine]").forEach((button) => button.addEventListener("click", () => { state.turbine = button.dataset.turbine === "both" ? "both" : Number(button.dataset.turbine); renderForecast(); }));
  document.querySelectorAll("[data-metric]").forEach((button) => button.addEventListener("click", () => { state.metric = button.dataset.metric; renderForecast(); }));
  document.querySelectorAll("[data-horizon]").forEach((button) => button.addEventListener("click", () => {
    state.horizon = Number(button.dataset.horizon);
    state.hour = Math.min(state.hour, state.horizon - 1);
    renderForecast();
  }));
  let resizeTimer;
  window.addEventListener("resize", () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(() => renderChart(), 100); });
  const downloadLabel = document.createElement("span");
  downloadLabel.textContent = "CSV · 48 ч · 2 турбины";
  const downloadIcon = $("download-button").querySelector("svg");
  $("download-button").replaceChildren(...(downloadIcon ? [downloadIcon, downloadLabel] : [downloadLabel]));
  $("download-button").title = "Полный прогноз на 48 часов для обеих турбин, независимо от выбранного вида графика";
  updateIssueNavigation();
  renderEvents();
  renderForecast();
  boot();
})();
