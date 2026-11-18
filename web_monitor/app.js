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
  const shutter = state.dome_status?.shutter;
  const status = [
    ["Shutter", h.dome ? (shutter && shutter !== "---" ? shutter : "Unknown") : "—"],
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
  batchRunning = state.batch_running === true;
  batchStopping = state.batch_stopping === true;
  acquisitionBusy = state.acquisition_busy === true;
  batchSchedule = state.batch_schedule;
  byId("batch-options").hidden = !batchSchedule;
  if (batchSchedule && !commandPending) renderSchedule();
  enabledTargets = state.plan.filter((row) => row.enabled).length;
  const exposure = batchStopping ? null : run?.exposure;
  const mode = batchRunning && run?.running ? run.mode : state.plan_mode ?? "";
  byId("mode").textContent = mode;
  byId("activity").textContent = batchStopping ? "Stopping…" :
    batchRunning ? (run?.running ? run.activity : "Finishing batch…") : "Idle";
  byId("batch-commands").hidden = !availableDevices.includes("batch");
  byId("batch-target-count").textContent = `${enabledTargets} enabled ${enabledTargets === 1 ? "target" : "targets"}`;
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
let batchRunning = false;
let batchStopping = false;
let acquisitionBusy = false;
let enabledTargets = 0;
let batchSchedule;
const batchOptionCheckboxes = [
  ["schedule-enabled", "enabled", false],
  ["schedule-pre-calib", "pre_calib", true],
  ["schedule-shutdown-calib", "shutdown_calib", true],
  ["schedule-park", "park", false],
  ["schedule-close-dome", "close_dome", false],
  ["schedule-warm-camera", "warm_camera", false],
];


function renderSchedule() {
  for (const [id, field] of batchOptionCheckboxes) {
    byId(id).checked = batchSchedule[field] === true;
  }
  for (const [id, value] of [["schedule-ha-min", batchSchedule.ha_min], ["schedule-ha-max", batchSchedule.ha_max]]) {
    const input = byId(id);
    if (document.activeElement !== input) input.value = value ?? "";
  }
  const start = byId("schedule-start");
  const times = ["", ...batchSchedule.start_times];
  if (!times.includes(batchSchedule.start_local)) times.push(batchSchedule.start_local);
  if (Array.from(start.options, (option) => option.value).join() !== times.join()) {
    start.replaceChildren(...times.map((time) => new Option(time || "No scheduled start", time)));
  }
  start.value = batchSchedule.start_local;
}
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
  if (!commandPending) byId("camera-binning").value = h.binning ?? "";
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
    (device === "batch" || typeof hardwareState[device] === "boolean");
}

function updateDeviceButtons() {
  byId("batch-run").disabled = !canCommand("batch") || batchRunning || acquisitionBusy || enabledTargets === 0;
  byId("batch-stop").disabled = !canCommand("batch") || !batchRunning || batchStopping;
  const scheduleLocked = !batchSchedule || !canCommand("batch") || batchRunning || acquisitionBusy;
  for (const [id, , requiresScheduling] of batchOptionCheckboxes) {
    byId(id).disabled = scheduleLocked || (requiresScheduling && !batchSchedule.enabled);
  }
  for (const id of ["schedule-ha-min", "schedule-ha-max", "schedule-start"]) {
    byId(id).disabled = scheduleLocked || !batchSchedule.enabled;
  }
  for (const device of devices) {
    byId(`${device}-connection`).disabled = !canCommand(device);
  }
  for (const id of ["camera-apply", "camera-cooler-on", "camera-cooler-off"]) {
    byId(id).disabled = !canCommand("camera") || !hardwareState.camera;
  }
  byId("camera-binning").disabled = !canCommand("camera");
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
  if (hardwareState.sync || window.confirm("Enable dome sync? The dome may move.")) {
    sendDeviceCommand("dome", {action: hardwareState.sync ? "sync_off" : "sync_on"});
  }
});

byId("batch-run").addEventListener("click", () => {
  if (window.confirm(`Run batch with ${byId("batch-target-count").textContent} in ${byId("mode").textContent.toLowerCase()} mode?`)) {
    sendDeviceCommand("batch", {action: "run"});
  }
});
byId("batch-stop").addEventListener("click", () => sendDeviceCommand("batch", {action: "stop"}));

for (const [id, field, property] of [
  ...batchOptionCheckboxes.map(([id, field]) => [id, field, "checked"]),
  ["schedule-ha-min", "ha_min", "valueAsNumber"],
  ["schedule-ha-max", "ha_max", "valueAsNumber"],
  ["schedule-start", "start_local", "value"],
]) {
  const input = byId(id);
  input.addEventListener("change", () => {
    if (input.disabled || !batchSchedule) return;
    sendDeviceCommand("batch", {action: "schedule", options: {[field]: input[property]}});
  });
  if (property === "valueAsNumber") {
    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter") input.blur();
    });
  }
}

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
    result.textContent = "No command result received. Check status before trying again.";
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
      : `${availableDevices.length ? "Controls enabled" : "Read-only"} · Updated ${clock(lastUpdate)}`;
  } catch {
    statusLive = false;
    byId("connection").textContent = `Connection lost · Last update ${clock(lastUpdate)}`;
  } finally {
    updateDeviceButtons();
    setTimeout(refresh, 2000);
  }
}

refresh();
