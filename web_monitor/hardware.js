import { byId, connected, onOff } from "./dom.js";
import { devices, session, canCommand, sendDeviceCommand } from "./commands.js";

const lampActions = byId("lamps-panel").querySelectorAll("[data-action]");
let domeSyncRunning = false;

const fitsPanel = byId("fits-panel");
const fitsImage = byId("fits-image");
const fitsStatus = byId("fits-status");
const testProgress = byId("camera-test-progress");
let viewerFrame;
let testExposure;

function updateTestProgress() {
  if (!testExposure) return;
  const {started, seconds, previousFrame} = testExposure;
  const elapsed = (performance.now() - started) / 1000;
  const frameReady = viewerFrame && viewerFrame.version !== previousFrame;
  const result = byId("camera-test-status");
  if (frameReady || elapsed >= seconds + 135) {
    testExposure = undefined;
    testProgress.hidden = true;
    result.textContent = frameReady ? "" : "No new preview received. Check the activity log.";
    updateHardwareControls();
    return;
  }
  testProgress.value = Math.min(99, 100 * elapsed / seconds);
  if (elapsed >= seconds && !result.textContent) result.textContent = "Waiting for image…";
  setTimeout(updateTestProgress, 100);
}

function renderViewer() {
  if (!fitsPanel.open || !viewerFrame) return;
  const source = `/api/viewer/${viewerFrame.version}`;
  if (fitsImage.getAttribute("src") === source) return;
  fitsImage.hidden = true;
  fitsStatus.textContent = "Loading frame…";
  fitsImage.src = source;
}

fitsPanel.addEventListener("toggle", renderViewer);
fitsImage.addEventListener("load", () => {
  if (fitsImage.getAttribute("src") !== `/api/viewer/${viewerFrame?.version}`) return;
  fitsStatus.textContent = `Max ADU: ${viewerFrame.max_adu}`;
  fitsImage.hidden = false;
});
fitsImage.addEventListener("error", () => {
  fitsStatus.textContent = "Waiting for the latest frame…";
  fitsImage.removeAttribute("src");
});

const domeOperations = byId("dome-operations");
const domeActions = domeOperations.querySelectorAll("[data-action]");

function renderHardwareState(id, value, format) {
  const item = byId(`${id}-state`);
  item.textContent = format(value);
  item.dataset.state = value == null ? "unknown" : value ? "active" : "inactive";
}

function renderDevices(state) {
  const h = state.hardware;
  if (h.camera && !session.hardwareState.camera) {
    byId("camera-setpoint").value = h.setpoint ?? h.saved_setpoint ?? "";
  }
  session.hardwareState = h;
  session.availableDevices = state.device_controls ?? [];
  for (const device of devices) {
    const available = session.availableDevices.includes(device);
    byId(`${device}-label`).hidden = available;
    byId(`${device}-toggle`).hidden = !available;
    renderHardwareState(device, h[device], connected);
    byId(`${device}-connection`).textContent = h[device] ? "Disconnect" : "Connect";
    if (!available) setPanelOpen(device, false);
  }
  const lampsAvailable = session.availableDevices.includes("lamps");
  byId("lamps-label").hidden = lampsAvailable;
  byId("lamps-toggle").hidden = !lampsAvailable;
  const lampsOn = h.tungsten || h.thorium;
  const lampsOff = h.tungsten === false && h.thorium === false;
  renderHardwareState("lamps", lampsOn ? true : lampsOff ? false : null, () => {
    if (h.tungsten && h.thorium) return "Tungsten + Thorium";
    if (h.tungsten) return "Tungsten";
    if (h.thorium) return "Thorium";
    return lampsOff ? "Off" : "—";
  });
  for (const button of lampActions) {
    const action = button.dataset.action;
    button.setAttribute("aria-pressed", String(action === "off" ? lampsOff : h[action] === true));
  }
  if (!lampsAvailable) setPanelOpen("lamps", false);
  if (!session.commandPending) byId("camera-binning").value = h.binning ?? "";
  byId("camera-temperature").hidden = !h.camera;
  byId("camera-binning-control").hidden = !h.camera;
  byId("camera-test-image").hidden = !h.camera;
  byId("camera-setpoint").placeholder = h.setpoint ?? h.saved_setpoint ?? "";
  domeOperations.hidden = !h.dome;

  const dome = state.dome_status ?? {};
  domeSyncRunning = dome.sync === true;
  const sync = domeSyncRunning ? (h.sync ? "On" : "Stopping") : (h.sync ? "Inactive" : "Off");
  byId("dome-detail").textContent =
    `Shutter ${dome.shutter ?? "—"} · ${dome.motion ?? "—"} · ${dome.azimuth ?? "—"} · Sync ${sync}`;
  byId("dome-sync").textContent = h.sync ? "Disable Sync" : "Enable Sync";
}

function setPanelOpen(device, open) {
  byId(`${device}-panel`).hidden = !open;
  byId(`${device}-toggle`).setAttribute("aria-expanded", String(open));
}

function toggleHardwarePanel(device) {
  const open = byId(`${device}-panel`).hidden;
  for (const panel of devices) setPanelOpen(panel, panel === device && open);
}

export function renderHardware(state) {
  viewerFrame = state.viewer;
  renderViewer();
  const h = state.hardware;
  const shutter = state.dome_status?.shutter;
  let shutterClass = "";
  if (h.dome) {
    if (shutter === "Closed") shutterClass = "status-alert";
    if (shutter === "Opening" || shutter === "Closing") shutterClass = "status-moving";
  }
  const status = [
    ["shutter", h.dome ? (shutter && shutter !== "---" ? shutter : "Unknown") : "—", shutterClass],
    ["dome-sync", h.slewing ? "Dome moving" : h.dome ? onOff(h.sync) : "—",
      h.dome && !h.slewing && h.sync === false ? "status-alert" : ""],
    ["cooler", h.cooler && h.setpoint != null ? `On · set ${h.setpoint} °C` : onOff(h.cooler),
      h.cooler === false ? "status-alert" : ""],
    ["temperature", h.temperature == null ? "—" : `${h.temperature.toFixed(1)} °C`],
    ["power", h.cooler_power == null ? "—" : `${h.cooler_power.toFixed(0)}%`],
    ["binning", h.binning ?? "—"],
  ];
  renderDevices(state);
  for (const [id, value, statusClass = ""] of status) {
    const reading = byId(`${id}-reading`);
    reading.textContent = value;
    reading.className = statusClass;
  }
  byId("status-camera-connection").textContent = connected(h.camera);
  byId("status-camera-binning").textContent = byId("binning-reading").textContent;
  byId("status-camera-temp").textContent = byId("temperature-reading").textContent;
  byId("slew-result").textContent = session.telescopeSlewActive
    ? "Resolving or slewing…"
    : state.telescope_slew_result ?? "";
}

export function updateHardwareControls() {
  const telescopeReady = canCommand("telescope") && session.hardwareState.telescope && !session.batchRunning;
  byId("slew-start").disabled = !telescopeReady || session.telescopeSlewActive || session.acquisitionBusy;
  byId("slew-stop").disabled = !telescopeReady;
  for (const button of lampActions) {
    button.disabled = !canCommand("lamps") || session.batchRunning || session.acquisitionBusy;
  }
  for (const device of devices) {
    byId(`${device}-connection`).disabled = !canCommand(device);
  }
  if (session.telescopeSlewActive) byId("telescope-connection").disabled = true;
  for (const id of ["camera-apply", "camera-cooler-on", "camera-cooler-off"]) {
    byId(id).disabled = !canCommand("camera") || !session.hardwareState.camera;
  }
  byId("camera-binning").disabled = !canCommand("camera");
  byId("camera-test-start").disabled =
    !canCommand("camera") || !session.hardwareState.camera || session.batchRunning || session.acquisitionBusy || session.telescopeSlewActive || !!testExposure;
  const domeDisabled = !canCommand("dome") || !session.hardwareState.dome;
  for (const button of domeActions) {
    const positionCommand = ["home", "park"].includes(button.dataset.action);
    button.disabled = domeDisabled || (positionCommand && domeSyncRunning);
  }
  byId("dome-sync").disabled = domeDisabled || (domeSyncRunning && !session.hardwareState.sync);
}

for (const device of devices) {
  byId(`${device}-toggle`).addEventListener("click", () => toggleHardwarePanel(device));
  byId(`${device}-connection`).addEventListener("click", () => {
    sendDeviceCommand(device, {action: session.hardwareState[device] ? "disconnect" : "connect"});
  });
}

byId("lamps-toggle").addEventListener("click", () => {
  setPanelOpen("lamps", byId("lamps-panel").hidden);
});

byId("slew-mode").addEventListener("change", () => {
  const byName = byId("slew-mode").value === "name";
  for (const field of ["name", "pm", "ra", "dec"]) {
    const hidden = byName !== (field === "name" || field === "pm");
    byId(`slew-${field}-label`).hidden = hidden;
    byId(`slew-${field}`).disabled = hidden;
  }
});
byId("telescope-slew").addEventListener("submit", (event) => {
  event.preventDefault();
  if (byId("slew-start").disabled) return;
  const command = byId("slew-mode").value === "name"
    ? {action: "slew_name", options: {name: byId("slew-name").value.trim(), apply_pm: byId("slew-pm").checked}}
    : {action: "slew_coords", options: {ra: byId("slew-ra").value.trim(), dec: byId("slew-dec").value.trim()}};
  sendDeviceCommand("telescope", command, "slew-command-result");
});
byId("slew-stop").addEventListener("click", () => {
  sendDeviceCommand("telescope", {action: "stop_slew"}, "slew-command-result");
});
for (const button of lampActions) {
  button.addEventListener("click", () => {
    setPanelOpen("lamps", false);
    byId("lamps-toggle").focus({preventScroll: true});
    sendDeviceCommand("lamps", {action: button.dataset.action});
  });
}

byId("camera-cooler-on").addEventListener("click", () => sendDeviceCommand("camera", {action: "cooler_on"}));
byId("camera-cooler-off").addEventListener("click", () => sendDeviceCommand("camera", {action: "cooler_off"}));
byId("camera-test-image").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (byId("camera-test-start").disabled) return;
  const exposure = byId("camera-test-seconds");
  if (!(exposure.valueAsNumber > 0)) {
    byId("camera-result").textContent = "Enter an exposure time greater than zero.";
    return;
  }
  const seconds = exposure.valueAsNumber;
  const previousFrame = viewerFrame?.version;
  byId("camera-test-status").textContent = "";
  if (await sendDeviceCommand("camera", {action: "test_image", seconds})) {
    testExposure = {started: performance.now(), seconds, previousFrame};
    testProgress.hidden = false;
    updateTestProgress();
    if (!fitsPanel.open) document.querySelector('[data-open-panel="fits-panel"]').click();
  }
});

byId("camera-temperature").addEventListener("submit", (event) => {
  event.preventDefault();
  const input = byId("camera-setpoint");
  if (!byId("camera-apply").disabled && input.reportValidity()) {
    sendDeviceCommand("camera", {action: "setpoint", setpoint: input.valueAsNumber});
  }
});
byId("camera-binning").addEventListener("change", (event) => {
  sendDeviceCommand("camera", {action: "binning", binning: Number(event.target.value)});
});
for (const button of domeActions) {
  button.addEventListener("click", () => {
    const action = button.dataset.action;
    if (action === "abort" || window.confirm(`${button.textContent}?`)) {
      sendDeviceCommand("dome", {action});
    }
  });
}
byId("dome-sync").addEventListener("click", () => {
  sendDeviceCommand("dome", {action: session.hardwareState.sync ? "sync_off" : "sync_on"});
});
