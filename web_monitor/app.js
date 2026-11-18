const byId = (id) => document.getElementById(id);
const clock = (value) => value ? new Date(value).toISOString().slice(11, 19) + " UT" : "—";
const connected = (value) => value == null ? "—" : value ? "Connected" : "Disconnected";
const onOff = (value) => value == null ? "—" : value ? "On" : "Off";

function element(tag, value) {
  const node = document.createElement(tag);
  node.textContent = value;
  return node;
}

function render(state) {
  const h = state.hardware;
  const status = [
    ["Dome sync", h.slewing ? "Dome moving" : h.dome ? onOff(h.sync) : "—"],
    ["Cooler", h.cooler && h.setpoint != null ? `On · set ${h.setpoint} °C` : onOff(h.cooler)],
    ["CCD", h.temperature == null ? "—" : `${h.temperature.toFixed(1)} °C`],
    ["Binning", h.binning ?? "—"], ["Tungsten", onOff(h.tungsten)], ["Thorium", onOff(h.thorium)],
  ];
  renderDevices(state);
  const hardware = byId("hardware");
  while (hardware.children.length > devices.length) hardware.lastElementChild.remove();
  hardware.append(...status.map(([label, value]) => {
    const item = document.createElement("div");
    item.append(element("dt", label), element("dd", value));
    return item;
  }));

  const run = state.run;
  const exposure = run?.exposure;
  byId("mode").textContent = run?.mode ?? state.plan_mode ?? "";
  byId("activity").textContent = run?.activity ?? "Idle";
  byId("exposure").hidden = byId("progress").hidden = !exposure;
  if (exposure) {
    byId("exposure").textContent =
      `${exposure.label} · Frame ${exposure.index} of ${exposure.total} · ${exposure.elapsed} of ${exposure.seconds} s`;
    byId("progress").max = Math.max(1, exposure.seconds);
    byId("progress").value = exposure.elapsed;
  }

  // Retain the completed run until the desktop plan changes.
  const samePlan = run && state.plan.length === run.rows.length &&
    state.plan.every((row, i) => Object.keys(row).every((key) => row[key] === run.rows[i][key]));
  const rows = run?.running || samePlan ? run.rows : state.plan;
  byId("targets").textContent = run?.running ? "Active batch targets" : "Targets";
  byId("rows").replaceChildren(...rows.map((row, index) => {
    const tr = document.createElement("tr");
    const active = run?.running && run.active === index;
    if (active) tr.classList.add("active");
    const reference = row.reference && row.reference_seconds && row.reference_frames
      ? `${row.reference} · ${row.reference_frames} × ${row.reference_seconds} s` : row.reference;
    tr.append(...[row.target, row.start_ut || "—", `${row.seconds} s`, row.frames,
      row.calibration ? "Yes" : "No", reference || "—",
      active ? "Active" : row.outcome ?? (row.enabled ? "Enabled" : "Disabled"),
    ].map((value) => element("td", value)));
    return tr;
  }));
  if (!rows.length) {
    const cell = element("td", "No batch targets loaded");
    cell.colSpan = 7;
    const row = document.createElement("tr");
    row.append(cell);
    byId("rows").append(row);
  }

  byId("events").replaceChildren(...state.events.slice().reverse().map((event) => {
    const item = document.createElement("li");
    const time = element("time", clock(event.time));
    time.dateTime = event.time;
    item.append(time, document.createTextNode(event.message));
    return item;
  }));
}

const devices = ["camera", "telescope", "dome"];
let hardwareState = {};
let availableDevices = [];
let commandPending = false;
let statusLive = false;
let domeSyncRunning = false;
const domeOperations = byId("dome-operations");
const domeActions = domeOperations.querySelectorAll("[data-action]");

function renderDevices(state) {
  const h = state.hardware;
  if (h.camera && !hardwareState.camera) {
    byId("camera-setpoint").value = h.setpoint ?? h.saved_setpoint ?? "";
  }
  hardwareState = h;
  availableDevices = state.device_controls ?? [];
  for (const device of devices) {
    const available = availableDevices.includes(device);
    byId(`${device}-label`).hidden = available;
    byId(`${device}-toggle`).hidden = !available;
    byId(`${device}-state`).textContent = connected(h[device]);
    byId(`${device}-connection`).textContent = h[device] ? "Disconnect" : "Connect";
    if (!available) setPanelOpen(device, false);
  }
  byId("camera-temperature").hidden = !h.camera;
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

function canCommand(device) {
  return statusLive && !commandPending && availableDevices.includes(device) &&
    typeof hardwareState[device] === "boolean";
}

function updateDeviceButtons() {
  for (const device of devices) {
    byId(`${device}-connection`).disabled = !canCommand(device);
  }
  for (const id of ["camera-apply", "camera-cooler-on", "camera-cooler-off"]) {
    byId(id).disabled = !canCommand("camera") || !hardwareState.camera;
  }
  const domeDisabled = !canCommand("dome") || !hardwareState.dome;
  for (const button of domeActions) {
    const positionCommand = ["home", "park"].includes(button.dataset.action);
    button.disabled = domeDisabled || (positionCommand && domeSyncRunning);
  }
  byId("dome-sync").disabled = domeDisabled || (domeSyncRunning && !hardwareState.sync);
}

for (const device of devices) {
  byId(`${device}-toggle`).addEventListener("click", () => {
    const open = byId(`${device}-panel`).hidden;
    for (const panel of devices) setPanelOpen(panel, panel === device && open);
  });
  byId(`${device}-connection`).addEventListener("click", () => {
    sendDeviceCommand(device, {action: hardwareState[device] ? "disconnect" : "connect"});
  });
}

byId("camera-cooler-on").addEventListener("click", () => sendDeviceCommand("camera", {action: "cooler_on"}));
byId("camera-cooler-off").addEventListener("click", () => sendDeviceCommand("camera", {action: "cooler_off"}));
byId("camera-temperature").addEventListener("submit", (event) => {
  event.preventDefault();
  const input = byId("camera-setpoint");
  if (!byId("camera-apply").disabled && input.reportValidity()) {
    sendDeviceCommand("camera", {action: "setpoint", setpoint: input.valueAsNumber});
  }
});
for (const button of domeActions) {
  button.addEventListener("click", () => {
    if (window.confirm(`${button.textContent}?`)) {
      sendDeviceCommand("dome", {action: button.dataset.action});
    }
  });
}
byId("dome-sync").addEventListener("click", () => {
  if (hardwareState.sync || window.confirm("Enable dome sync? The dome may move.")) {
    sendDeviceCommand("dome", {action: hardwareState.sync ? "sync_off" : "sync_on"});
  }
});

async function sendDeviceCommand(device, command) {
  if (!canCommand(device)) return;
  const result = byId(`${device}-result`);
  result.textContent = "Waiting for Spectro Capture…";
  commandPending = true;
  updateDeviceButtons();
  try {
    const response = await fetch(`/api/${device}`, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(command),
      signal: AbortSignal.timeout(15000),
    });
    result.textContent = (await response.json()).message;
  } catch {
    result.textContent = "No command result received. Check device status before trying again.";
  } finally {
    commandPending = false;
    statusLive = false;
    updateDeviceButtons();
  }
}

let lastUpdate;
async function refresh() {
  try {
    const response = await fetch("/api/status", {
      cache: "no-store",
      signal: AbortSignal.timeout(5000),
    });
    if (!response.ok) throw new Error(response.status);
    const state = await response.json();
    render(state);
    lastUpdate = state.updated;
    const stale = !lastUpdate || Date.now() - Date.parse(lastUpdate) > 10000;
    statusLive = !stale;
    byId("connection").textContent = stale
      ? `Waiting for desktop refresh · ${clock(lastUpdate)}`
      : `${availableDevices.length ? "Hardware controls enabled" : "Read-only"} · Updated ${clock(lastUpdate)}`;
  } catch {
    statusLive = false;
    byId("connection").textContent = `Connection lost · Last update ${clock(lastUpdate)}`;
  } finally {
    updateDeviceButtons();
    setTimeout(refresh, 2000);
  }
}

refresh();
