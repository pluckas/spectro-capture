import { byId } from "./dom.js";
import { session, canCommand, sendDeviceCommand } from "./commands.js";
import { statusDetails } from "./system-status.js";

const guideControls = byId("guide-controls");
const guideSeconds = byId("guide-seconds");
const guideImage = byId("guide-field-image");
const guideStarImage = byId("guide-star-image");
const guidingPanel = byId("guiding-panel");
const statusGuideImage = byId("status-guide-image");
export const watchingGuide = () => (guidingPanel.open || !statusDetails.hidden) && !document.hidden
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
  renderGuideCrop(statusGuideImage, state.guide_star, !statusDetails.hidden);
  const guideState = state.guide_app_state;
  byId("status-guide-state").textContent = [
    guideState ? "Connected" : "PHD2 status unavailable",
    guideState,
    session.batchRunning && !state.batch_stopping ? state.guide_phase : null,
  ].filter(Boolean).join(" · ");
  byId("status-guide-caption").textContent = state.guide_snapshot_message || state.guide_star_message || "";
  if (watchingGuide()
      && !guideBusy && !session.commandPending && Date.now() >= nextGuideRefresh) {
    nextGuideRefresh = Date.now() + guideRefreshMs;
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
