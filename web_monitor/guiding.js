import { byId, clock, element } from "./dom.js";
import { session, canCommand, sendDeviceCommand } from "./commands.js";

const guideControls = byId("guide-controls");
const guideSeconds = byId("guide-seconds");
const guideImage = byId("guide-field-image");
const guideStarImage = byId("guide-star-image");
const guidingPanel = byId("guiding-panel");
const statusGuideImage = byId("status-guide-image");
export const watchingGuide = () => !document.hidden
  && session.availableDevices.includes("guiding");

function renderGuideCrop(image, version, visible) {
  if (!visible || !version) {
    image.hidden = true;
    image.removeAttribute("src");
    return;
  }
  const source = `/api/guide-star/${version}`;
  if (image.getAttribute("src") !== source) image.src = source;
}
export const guideRefreshMs = 1000;
let nextGuideRefresh = 0;
let guideBusy = false;
let guideFibreInitialized = false;

function renderGuideLog(list, events, emptyMessage) {
  const key = JSON.stringify(events);
  if (list.dataset.logKey === key) return;
  list.dataset.logKey = key;
  if (!events.length && emptyMessage) {
    const item = element("li", emptyMessage);
    item.className = "guide-placeholder";
    list.replaceChildren(item);
    return;
  }
  list.replaceChildren(...events.map((event) => {
    const item = document.createElement("li");
    item.append(element("time", clock(event.time)), document.createTextNode(event.message));
    return item;
  }));
}

let guideGraphKey;
function renderGuideGraph(samples, guiding) {
  const canvas = byId("guide-error-chart");
  const placeholder = byId("guide-error-placeholder");
  canvas.hidden = !guiding || samples.length < 2;
  placeholder.hidden = !canvas.hidden;
  if (canvas.hidden) {
    placeholder.textContent = guiding ? "Waiting for guide errors" : "Waiting for guiding";
    byId("guide-error-scale").textContent = "";
    guideGraphKey = undefined;
    return;
  }
  const key = JSON.stringify(samples);
  if (guideGraphKey === key) return;
  guideGraphKey = key;
  const ctx = canvas.getContext("2d");
  const {width, height} = canvas;
  const limit = Math.max(0.5, ...samples.flat().map(Math.abs));
  byId("guide-error-scale").textContent = `±${limit.toFixed(1)} px`;
  ctx.clearRect(0, 0, width, height);
  ctx.strokeStyle = "#737373";
  ctx.beginPath();
  ctx.moveTo(0, height / 2);
  ctx.lineTo(width, height / 2);
  ctx.stroke();
  for (const [axis, color] of [[0, "#51baac"], [1, "#d581b7"]]) {
    ctx.strokeStyle = color;
    ctx.lineWidth = 2;
    ctx.beginPath();
    samples.forEach((sample, index) => {
      const x = index * width / (samples.length - 1);
      const y = height / 2 - (height / 2 - 4) * sample[axis] / limit;
      if (index === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.stroke();
  }
}

export function renderGuiding(state) {
  guideControls.hidden = !session.availableDevices.includes("guiding");
  guideBusy = state.guide_busy === true;
  byId("guide-message").textContent = state.guide_message ?? "";
  byId("guide-snapshot-status").textContent = state.guide_snapshot_message ?? "";
  if (guidingPanel.open && state.guide_frame) {
    const source = `/api/guide-field/${state.guide_frame}`;
    if (guideImage.getAttribute("src") !== source) guideImage.src = source;
  }
  byId("guide-star-status").textContent = state.guide_star_message ?? "Waiting for a fibre-centred frame…";
  renderGuideCrop(guideStarImage, state.guide_star, guidingPanel.open);
  const guideState = state.guide_app_state;
  const active = Boolean(guideState && guideState !== "Stopped");
  byId("status-guide-detail").hidden = !active;
  renderGuideCrop(statusGuideImage, state.guide_star, active);
  const events = state.guide_events ?? [];
  renderGuideLog(byId("guide-events"), events.slice(-4), "No recent guide events");
  if (byId("guide-log-panel").open) renderGuideLog(byId("full-guide-events"), [...events].reverse());
  renderGuideGraph(state.guide_errors ?? [], guideState === "Guiding");
  const status = byId("status-guide-state");
  status.dataset.guiding = String(guideState === "Guiding");
  status.textContent = [
    guideState ? "Connected" : "PHD2 status unavailable",
    guideState,
    session.batchRunning && !state.batch_stopping
      ? state.guide_phase ?? (guideState === "Guiding" && state.guide_on_fibre ? "On fibre" : null) : null,
  ].filter(Boolean).join(" · ");
  byId("status-guide-caption").textContent = guideState && guideState !== "Stopped"
    ? state.guide_snapshot_message || (state.guide_star ? "" : state.guide_star_message) || "" : "";
  if (watchingGuide()
      && !guideBusy && !session.commandPending && Date.now() >= nextGuideRefresh) {
    nextGuideRefresh = Date.now() + (!guidingPanel.open && (!guideState || guideState === "Stopped") ? 4000 : guideRefreshMs);
    fetch("/api/guiding", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({action: "snapshot"}),
      signal: AbortSignal.timeout(5000),
    }).catch(() => {});
  }
  if (!guideSeconds.options.length && state.guide_exposure_choices) {
    guideSeconds.replaceChildren(new Option("Select exposure", ""), ...state.guide_exposure_choices.map(
      (value) => new Option(value, value)));
  }
  if (!guideFibreInitialized && state.guide_fibre) {
    [byId("guide-fibre-x").value, byId("guide-fibre-y").value] = state.guide_fibre;
    guideFibreInitialized = true;
  }
}

guideImage.addEventListener("load", () => { guideImage.hidden = false; });
guideImage.addEventListener("error", () => { guideImage.hidden = true; });
for (const image of [guideStarImage, statusGuideImage]) {
  image.addEventListener("load", () => { if (image.hasAttribute("src")) image.hidden = false; });
  image.addEventListener("error", () => { image.hidden = true; });
}

const guideActionButtons = guidingPanel.querySelectorAll("[data-guide-action]");
for (const button of guideActionButtons) {
  button.addEventListener("click", () => sendDeviceCommand("guiding", {action: button.dataset.guideAction}));
}
byId("guide-exposure").addEventListener("submit", (event) => {
  event.preventDefault();
  sendDeviceCommand("guiding", {action: "exposure", options: {seconds: Number(guideSeconds.value)}});
});
byId("guide-fibre").addEventListener("submit", (event) => {
  event.preventDefault();
  sendDeviceCommand("guiding", {action: "save_fibre", options: {
    x: byId("guide-fibre-x").valueAsNumber,
    y: byId("guide-fibre-y").valueAsNumber,
  }});
});

export function updateGuidingControls() {
  for (const button of [...guideActionButtons, ...guideControls.querySelectorAll('button[type="submit"]')]) {
    button.disabled = !canCommand("guiding") || guideBusy || session.batchRunning;
  }
}
