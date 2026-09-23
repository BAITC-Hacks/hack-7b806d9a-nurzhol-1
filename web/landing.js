"use strict";

(() => {
  const chart = document.getElementById("preview-chart");
  const container = document.getElementById("preview-plot");
  const status = document.getElementById("preview-status");
  const buttons = [...document.querySelectorAll("[data-preview-metric]")];
  const HOUR = 3600000;
  const colors = ["#235be8", "#a75f10"];
  let series = [], metric = "power", hour = 36, selectHour = () => {};
  const number = (value) => value.toLocaleString("ru-RU", { minimumFractionDigits: metric === "power" ? 3 : 2, maximumFractionDigits: metric === "power" ? 3 : 2 });
  const announce = () => {
    document.getElementById("preview-announcement").textContent = `${document.getElementById("preview-time").textContent}. ${metric === "power" ? "Мощность по шкале 0–1" : "Ветер"}. Турбина 1: ${document.getElementById("preview-t1").textContent}. Турбина 2: ${document.getElementById("preview-t2").textContent}.`;
  };
  const node = (tag, attrs, text) => {
    const element = document.createElementNS("http://www.w3.org/2000/svg", tag);
    Object.entries(attrs).forEach(([key, value]) => element.setAttribute(key, value));
    if (text !== undefined) element.textContent = text;
    return element;
  };

  function render() {
    if (!series.length) return;
    const width = container.clientWidth, height = container.clientHeight;
    const left = 36, right = 12, top = 28, bottom = 36;
    const plotWidth = width - left - right, plotHeight = height - top - bottom;
    const key = metric === "power" ? "power_normalized" : "wind_speed_ms";
    const maxY = metric === "power" ? 1 : Math.max(4, Math.ceil(Math.max(...series.flatMap(rows => rows.map(row => row[key]))) / 4) * 4);
    const x = (h) => left + h / 48 * plotWidth;
    const y = (value) => top + plotHeight * (1 - value / maxY);
    const textStyle = { fill: "#607087", "font-size": 10, "font-family": "monospace" };
    chart.setAttribute("viewBox", `0 0 ${width} ${height}`);
    chart.replaceChildren(
      node("title", { id: "preview-title" }, `${metric === "power" ? "Мощность" : "Ветер"}: прогноз двух турбин на 1–2 февраля 2026`),
      node("desc", { id: "preview-description" }, "Синяя линия — Т1, коричневая пунктирная — Т2. Выберите час указателем или стрелками влево и вправо. Точные значения — под графиком. Время UTC+5.")
    );
    chart.append(node("text", { x: left, y: 12, ...textStyle }, metric === "power" ? "Мощность · 0–1" : "Ветер · м/с"));
    chart.append(node("rect", { x: x(24), y: top, width: plotWidth / 2, height: plotHeight, fill: "#f7f9fd" }));
    for (let step = 0; step <= 4; step++) {
      const value = maxY * step / 4;
      chart.append(node("line", { x1: left, x2: width - right, y1: y(value), y2: y(value), stroke: step === 0 ? "#bac7d8" : "#e6ebf3" }));
      chart.append(node("text", { x: left - 8, y: y(value) + 3, "text-anchor": "end", ...textStyle }, value.toLocaleString("ru-RU", { maximumFractionDigits: 2 })));
    }
    chart.append(node("line", { x1: x(24), x2: x(24), y1: top, y2: y(0), stroke: "#c3cfdf", "stroke-dasharray": "3 4" }));
    [0, 12, 24, 36, 48].forEach(h => {
      const label = h === 0 ? "01.02" : h === 24 ? "02.02" : h === 48 ? "03.02" : "12:00";
      chart.append(node("text", { x: x(h), y: height - 12, "text-anchor": h === 0 ? "start" : h === 48 ? "end" : "middle", ...textStyle }, label));
    });
    series.forEach((rows, index) => chart.append(node("path", {
      d: rows.map((row, h) => `${h ? "L" : "M"}${x(h + .5).toFixed(2)},${y(row[key]).toFixed(2)}`).join(" "),
      fill: "none", stroke: colors[index], "stroke-width": 2.2, "stroke-linejoin": "round", "stroke-linecap": "round", ...(index === 1 ? { "stroke-dasharray": "5 4" } : {})
    })));
    const crosshair = node("line", { y1: top, y2: y(0), stroke: "#8091aa", "stroke-dasharray": "3 4" });
    chart.append(crosshair);
    const dots = colors.map(color => node("circle", { r: 3.5, fill: "#fff", stroke: color, "stroke-width": 2 }));
    chart.append(...dots);
    selectHour = (next) => {
      hour = Math.max(0, Math.min(47, next));
      crosshair.setAttribute("x1", x(hour + .5));
      crosshair.setAttribute("x2", x(hour + .5));
      series.forEach((rows, index) => {
        dots[index].setAttribute("cx", x(hour + .5));
        dots[index].setAttribute("cy", y(rows[hour][key]));
        document.getElementById(`preview-t${index + 1}`).textContent = `${number(rows[hour][key])}${metric === "wind" ? " м/с" : ""}`;
      });
      const local = series[0][hour].valid_time_local;
      document.getElementById("preview-time").textContent = `${local.slice(8, 10)}.${local.slice(5, 7)} · ${local.slice(11, 16)} UTC+5`;
    };
    const hitArea = node("rect", { x: left, y: top, width: plotWidth, height: plotHeight, fill: "transparent" });
    const inspect = (event) => {
      const bounds = chart.getBoundingClientRect();
      selectHour(Math.floor(((event.clientX - bounds.left) * width / bounds.width - left) / plotWidth * 48));
    };
    hitArea.addEventListener("pointermove", inspect);
    hitArea.addEventListener("click", inspect);
    chart.append(hitArea);
    selectHour(hour);
  }

  async function load() {
    status.textContent = "Загружаем пример из архива…";
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 15000);
    try {
      const response = await fetch("/api/forecast?issue_date=2026-01-31", { signal: controller.signal });
      if (!response.ok) throw new Error("forecast-unavailable");
      const data = await response.json();
      const rowsByTurbine = [1, 2].map(turbine => data.rows.filter(row => row.turbine_id === turbine).sort((a, b) => Date.parse(a.valid_time_utc) - Date.parse(b.valid_time_utc)));
      const start = Date.parse("2026-01-31T19:00:00Z");
      if (rowsByTurbine.some(rows => rows.length !== 48 || rows.some((row, index) => Date.parse(row.valid_time_utc) !== start + index * HOUR || !Number.isFinite(row.power_normalized) || !Number.isFinite(row.wind_speed_ms) || typeof row.valid_time_local !== "string"))) throw new Error("incomplete-forecast");
      series = rowsByTurbine;
      chart.removeAttribute("hidden");
      status.hidden = true;
      buttons.forEach(button => { button.disabled = false; });
      render();
    } catch {
      status.textContent = "Пример временно недоступен. Рабочую панель можно открыть по кнопке ниже.";
      const retry = document.createElement("button");
      retry.type = "button";
      retry.textContent = "Повторить загрузку";
      retry.addEventListener("click", load);
      status.append(retry);
    } finally { clearTimeout(timeout); }
  }
  buttons.forEach(button => button.addEventListener("click", () => {
    metric = button.dataset.previewMetric;
    buttons.forEach(item => item.setAttribute("aria-pressed", String(item === button)));
    render();
    announce();
  }));
  chart.addEventListener("keydown", event => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key) || !series.length) return;
    event.preventDefault();
    selectHour(event.key === "Home" ? 0 : event.key === "End" ? 47 : hour + (event.key === "ArrowLeft" ? -1 : 1));
    announce();
  });
  new ResizeObserver(render).observe(container);
  load();
})();
