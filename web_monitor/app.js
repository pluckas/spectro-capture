const byId = (id) => document.getElementById(id);
const clock = (value) => value ? new Date(value).toISOString().slice(11, 19) + " UT" : "—";
const connected = (value) => value == null ? "—" : value ? "Connected" : "Disconnected";
const onOff = (value) => value == null ? "—" : value ? "On" : "Off";

const themeDialog = byId("theme-dialog");
const themeChoices = [...themeDialog.querySelectorAll("[data-theme-choice]")];

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  for (const choice of themeChoices) {
    choice.setAttribute("aria-pressed", String(choice.dataset.themeChoice === theme));
  }
}

try {
  const savedTheme = localStorage.getItem("spectro-capture-theme");
  if (themeChoices.some((choice) => choice.dataset.themeChoice === savedTheme)) {
    applyTheme(savedTheme);
  }
} catch {
  // The default theme works when browser storage is unavailable.
}

byId("theme-open").addEventListener("click", () => {
  themeDialog.showModal();
  themeDialog.querySelector('[aria-pressed="true"]').focus();
});
for (const choice of themeChoices) {
  choice.addEventListener("click", () => {
    const theme = choice.dataset.themeChoice;
    applyTheme(theme);
    try {
      localStorage.setItem("spectro-capture-theme", theme);
    } catch {
      // Keep the selection for this page even when it cannot be saved.
    }
  });
}

const workspacePanels = byId("workspace-panels");
for (const button of document.querySelectorAll("[data-open-panel]")) {
  button.addEventListener("click", () => {
    const panel = byId(button.dataset.openPanel);
    if (workspacePanels.firstElementChild !== panel) workspacePanels.prepend(panel);
    panel.open = true;
    const summary = panel.querySelector("summary");
    summary.focus({preventScroll: true});
    summary.scrollIntoView({block: "nearest"});
  });
}

function element(tag, value) {
  const node = document.createElement(tag);
  node.textContent = value;
  return node;
}

const fitsPanel = byId("fits-panel");
const fitsImage = byId("fits-image");
const fitsStatus = byId("fits-status");
let viewerFrame;

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

function render(state) {
  viewerFrame = state.viewer;
  renderViewer();
  const h = state.hardware;
  const shutter = state.dome_status?.shutter;
  const status = [
    ["Shutter", h.dome ? (shutter && shutter !== "---" ? shutter : "Unknown") : "—"],
    ["Dome sync", h.slewing ? "Dome moving" : h.dome ? onOff(h.sync) : "—"],
    ["Cooler", h.cooler && h.setpoint != null ? `On · set ${h.setpoint} °C` : onOff(h.cooler)],
    ["CCD", h.temperature == null ? "—" : `${h.temperature.toFixed(1)} °C`],
    ["Binning", h.binning ?? "—"],
  ];
  renderDevices(state);
  const hardware = byId("hardware");
  for (const item of hardware.querySelectorAll(".hardware-reading")) item.remove();
  byId("tungsten-status").before(...status.map(([label, value]) => {
    const item = document.createElement("div");
    item.className = "hardware-reading";
    item.append(element("dt", label), element("dd", value));
    return item;
  }));

  const run = state.run;
  batchRunning = state.batch_running === true;
  renderGuiding(state);
  batchStopping = state.batch_stopping === true;
  acquisitionBusy = state.acquisition_busy === true;
  telescopeSlewActive = state.telescope_slew_active === true;
  byId("slew-result").textContent = telescopeSlewActive
    ? "Resolving or slewing…"
    : state.telescope_slew_result ?? "";
  batchSchedule = state.batch_schedule;
  renderCalibration(state.calibration_settings);
  byId("batch-options").hidden = !batchSchedule;
  if (batchSchedule && !commandPending) renderSchedule();
  targetCount = state.plan.length;
  targetListRevision = state.target_list_revision;
  batchRunRevision = state.batch_run_revision;
  byId("batch-csv-controls").hidden = !availableDevices.includes("batch");
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
  renderTargets(rows, run, state.target_edits ?? []);

  byId("events").replaceChildren(...state.events.slice().reverse().map((event) => {
    const item = document.createElement("li");
    const time = element("time", clock(event.time));
    time.dateTime = event.time;
    item.append(time, document.createTextNode(event.message));
    return item;
  }));
}

let targetTableKey;
let activeTargetEditor;
let targetDrag;

function renderTargets(rows, run, edits) {
  const editable = availableDevices.includes("batch") && !batchRunning && !run?.running;
  const key = JSON.stringify([rows, editable ? edits : null, run?.running, run?.active]);
  if (key === targetTableKey) return;
  targetTableKey = key;
  endTargetDrag();
  const table = byId("rows");
  table.replaceChildren(...rows.map((row, index) => {
    const tr = document.createElement("tr");
    const active = run?.running && run.active === index;
    if (active) tr.classList.add("active");
    const reference = row.reference && row.reference_seconds && row.reference_frames
      ? `${row.reference} · ${row.reference_frames} × ${row.reference_seconds} s` : row.reference;
    const edit = editable ? edits[index] : null;
    const name = element("td", row.target);
    if (edit) {
      tr.dataset.targetId = edit.id;
      const trigger = element("button", row.target);
      trigger.type = "button";
      trigger.className = "target-control";
      trigger.setAttribute("aria-label", `Edit name and coordinates for ${row.target}`);
      trigger.addEventListener("click", () => openTargetEditor(edit, row.target, tr, null, trigger));
      name.replaceChildren(trigger);
    }
    const referenceCell = element("td", reference || "—");
    if (edit) {
      const trigger = element("button", reference || "Add reference");
      trigger.type = "button";
      trigger.className = "target-control";
      trigger.setAttribute("aria-label", `Edit reference for ${row.target}`);
      trigger.addEventListener("click", () => openTargetEditor(edit, row.target, tr, {
        name: row.reference ?? "", seconds: row.reference_seconds ?? "", frames: row.reference_frames ?? "",
      }, trigger));
      referenceCell.replaceChildren(trigger);
    }
    tr.append(targetMoveHandle(row, edit, tr), name,
      targetField(row, edit, "start_ut", "Start (UT)", row.start_ut || "—"),
      targetField(row, edit, "seconds", "Exposure (s)", `${row.seconds} s`),
      targetField(row, edit, "frames", "Frames", row.frames),
      targetField(row, edit, "calibration", "Calibration", row.calibration ? "Yes" : "No"),
      targetField(row, edit, "enabled", "Enabled", row.enabled ? "Yes" : "No"),
      referenceCell,
      element("td", active ? "Active" : row.outcome ?? "—"));
    return tr;
  }));
  if (!rows.length) {
    const cell = element("td", "No batch targets loaded");
    cell.colSpan = 9;
    const row = document.createElement("tr");
    row.append(cell);
    table.append(row);
  }
  if (activeTargetEditor) {
    const id = activeTargetEditor.edit.id;
    const target = [...table.children].find((row) => row.dataset.targetId === id);
    if (!editable || (id && !target)) {
      closeTargetEditor();
    } else if (target) {
      target.after(activeTargetEditor.row);
    } else {
      table.append(activeTargetEditor.row);
    }
  }
}

function targetMoveHandle(row, edit, tr) {
  const cell = document.createElement("td");
  cell.className = "target-move-cell";
  if (!edit) return cell;
  const handle = element("button", "⠿");
  handle.type = "button";
  handle.className = "target-move-handle";
  handle.disabled = true;
  handle.title = "Drag to reorder, or use Up and Down arrow keys";
  handle.setAttribute("aria-label", `Reorder ${row.target}`);
  handle.addEventListener("pointerdown", (event) => {
    if (handle.disabled || targetDrag || event.button !== 0) return;
    event.preventDefault();
    handle.setPointerCapture(event.pointerId);
    targetDrag = {handle, tr, revision: targetListRevision,
      pointer: event.pointerId, x: event.clientX, y: event.clientY};
    tr.classList.add("target-dragging");
    targetDrag.frame = requestAnimationFrame(updateTargetDrag);
  });
  handle.addEventListener("pointermove", (event) => {
    if (targetDrag?.handle !== handle || event.pointerId !== targetDrag.pointer) return;
    targetDrag.x = event.clientX;
    targetDrag.y = event.clientY;
  });
  handle.addEventListener("pointerup", (event) => {
    if (targetDrag?.handle !== handle || event.pointerId !== targetDrag.pointer) return;
    const destination = targetDropAt(event.clientX, event.clientY, tr);
    const revision = targetDrag.revision;
    endTargetDrag();
    if (destination && !handle.disabled) {
      moveTarget(edit.id, destination.row.dataset.targetId, destination.after, revision);
    }
  });
  handle.addEventListener("lostpointercapture", () => {
    if (targetDrag?.handle === handle) endTargetDrag();
  });
  handle.addEventListener("keydown", (event) => {
    if (handle.disabled || !["ArrowUp", "ArrowDown"].includes(event.key)) return;
    event.preventDefault();
    const after = event.key === "ArrowDown";
    const neighbor = after ? tr.nextElementSibling : tr.previousElementSibling;
    if (neighbor?.dataset.targetId) {
      moveTarget(edit.id, neighbor.dataset.targetId, after, targetListRevision);
    }
  });
  cell.append(handle);
  return cell;
}

function targetDropAt(x, y, source) {
  const row = document.elementFromPoint(x, y)?.closest("tr[data-target-id]");
  if (!row || row === source || row.parentElement !== byId("rows")) return null;
  const bounds = row.getBoundingClientRect();
  return {row, after: y >= bounds.top + bounds.height / 2};
}

function updateTargetDrag() {
  if (!targetDrag) return;
  const {x, y, tr, handle} = targetDrag;
  if (handle.disabled) {
    endTargetDrag();
    return;
  }
  const edge = 48;
  if (y < edge) window.scrollBy(0, -12);
  else if (y > window.innerHeight - edge) window.scrollBy(0, 12);
  targetDrag.mark?.classList.remove("drop-before", "drop-after");
  const target = targetDropAt(x, y, tr);
  targetDrag.mark = target?.row;
  target?.row.classList.add(target.after ? "drop-after" : "drop-before");
  targetDrag.frame = requestAnimationFrame(updateTargetDrag);
}

function endTargetDrag() {
  if (!targetDrag) return;
  const {handle, tr, mark, pointer, frame} = targetDrag;
  targetDrag = null;
  cancelAnimationFrame(frame);
  tr.classList.remove("target-dragging");
  mark?.classList.remove("drop-before", "drop-after");
  if (handle.hasPointerCapture(pointer)) handle.releasePointerCapture(pointer);
}

function moveTarget(id, destination, after, revision) {
  sendDeviceCommand("batch", {action: "move_target", edit: {id, destination, after, revision}},
    "batch-csv-result");
}

function closeTargetEditor() {
  if (!activeTargetEditor) return;
  activeTargetEditor.row.remove();
  activeTargetEditor = null;
}

function openTargetEditor(edit = {}, name = "", targetRow = null, reference = null, trigger = null) {
  closeTargetEditor();
  const kind = reference ? "reference" : "identity";
  const resultId = `target-${kind}-result`;
  const row = byId(`target-${kind}-template`).content.firstElementChild.cloneNode(true);
  const form = row.querySelector("form");
  const values = reference ?? {name, ra: edit.ra ?? "", dec: edit.dec ?? ""};
  for (const [field, value] of Object.entries(values)) {
    form.elements.namedItem(field).value = value;
  }
  activeTargetEditor = {row, edit};
  if (targetRow) targetRow.after(row);
  else byId("rows").append(row);
  const cancel = () => {
    closeTargetEditor();
    updateDeviceButtons();
    (trigger ?? targetRow?.querySelector("button") ?? byId("batch-add-target")).focus();
  };
  const target = edit.id ? {id: edit.id, revision: edit.revision} : {};
  form.querySelector("[data-cancel]").addEventListener("click", cancel);
  const remove = form.querySelector("[data-remove]");
  if (reference ? Object.values(reference).some(Boolean) : edit.id) {
    remove.hidden = false;
    remove.addEventListener("click", async () => {
      const message = reference ? `Remove the reference from ${name}?` : `Remove ${name} from the batch?`;
      if (!window.confirm(message)) return;
      const command = reference
        ? {action: "save_target_reference", edit: {...target, reference: null}}
        : {action: "remove_target", edit: target};
      const removed = await sendDeviceCommand("batch", command, resultId);
      if (removed && activeTargetEditor?.row === row) cancel();
    });
  }
  form.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !commandPending) {
      event.preventDefault();
      cancel();
    }
  });
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (form.querySelector('[type="submit"]').disabled) return;
    const fields = Object.fromEntries(new FormData(form));
    const command = reference
      ? {action: "save_target_reference", edit: {...target, reference: fields}}
      : {action: "save_target_identity", edit: {...target, ...fields}};
    const saved = await sendDeviceCommand("batch", command, resultId);
    if (saved && activeTargetEditor?.row === row) cancel();
  });
  updateDeviceButtons();
  form.elements.namedItem("name").focus();
}

function targetField(row, edit, field, label, display) {
  const cell = element("td", display);
  if (!edit) return cell;
  const commit = (value) => sendDeviceCommand("batch",
    {action: "edit_target", edit: {...edit, field, value}}, "batch-csv-result");
  const control = typeof row[field] === "boolean" ? document.createElement("input") : element("button", display);
  control.className = "target-control";
  control.setAttribute("aria-label", `${row.target}: ${label}`);
  if (typeof row[field] === "boolean") {
    control.type = "checkbox";
    control.checked = row[field];
    control.addEventListener("change", () => {
      const value = control.checked;
      control.checked = row[field];
      commit(value);
    });
  } else {
    control.type = "button";
    control.addEventListener("click", () => {
      const input = document.createElement("input");
      input.className = "target-control target-editor";
      input.setAttribute("aria-label", `${row.target}: ${label}`);
      input.value = row[field];
      if (field === "start_ut") input.placeholder = "HH:MM";
      if (field === "seconds") input.inputMode = "decimal";
      if (field === "frames") input.inputMode = "numeric";
      input.addEventListener("blur", () => input.replaceWith(control));
      input.addEventListener("keydown", (event) => {
        if (!["Enter", "Escape"].includes(event.key)) return;
        event.preventDefault();
        const value = input.value.trim();
        input.replaceWith(control);
        control.focus();
        if (event.key === "Enter" && value !== row[field]) commit(value);
      });
      control.replaceWith(input);
      input.focus();
      input.select();
    });
  }
  cell.replaceChildren(control);
  return cell;
}

const devices = ["camera", "telescope", "dome"];
const lampLabels = ["tungsten", "thorium"];
const hardwarePanels = [...devices, "lamps"];
const lampActions = byId("lamps-panel").querySelectorAll("[data-action]");
let hardwareState = {};
let availableDevices = [];
let commandPending = false;
let statusLive = false;
let domeSyncRunning = false;
let batchRunning = false;
let batchStopping = false;
let acquisitionBusy = false;
let telescopeSlewActive = false;
let enabledTargets = 0;
let targetCount = 0;
let targetListRevision;
let batchRunRevision;
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
  byId("telescope-menu").disabled = !availableDevices.includes("telescope");
  for (const device of devices) {
    const available = availableDevices.includes(device);
    byId(`${device}-label`).hidden = available;
    byId(`${device}-toggle`).hidden = !available;
    byId(`${device}-state`).textContent = connected(h[device]);
    byId(`${device}-connection`).textContent = h[device] ? "Disconnect" : "Connect";
    if (!available) setPanelOpen(device, false);
  }
  const lampsAvailable = availableDevices.includes("lamps");
  for (const lamp of lampLabels) {
    byId(`${lamp}-label`).hidden = lampsAvailable;
    byId(`${lamp}-toggle`).hidden = !lampsAvailable;
    byId(`${lamp}-state`).textContent = onOff(h[lamp]);
  }
  if (!lampsAvailable) setPanelOpen("lamps", false);
  if (!commandPending) byId("camera-binning").value = h.binning ?? "";
  byId("camera-temperature").hidden = !h.camera;
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
  if (device === "telescope") byId("telescope-menu").setAttribute("aria-expanded", String(open));
  for (const label of device === "lamps" ? lampLabels : [device]) {
    byId(`${label}-toggle`).setAttribute("aria-expanded", String(open));
  }
}

function toggleHardwarePanel(device) {
  const open = byId(`${device}-panel`).hidden;
  for (const panel of hardwarePanels) setPanelOpen(panel, panel === device && open);
}

byId("telescope-menu").addEventListener("click", () => {
  for (const panel of hardwarePanels) setPanelOpen(panel, panel === "telescope");
  byId("telescope-panel").scrollIntoView({block: "nearest"});
});

function canCommand(device) {
  return statusLive && !commandPending && availableDevices.includes(device) &&
    (!devices.includes(device) || typeof hardwareState[device] === "boolean");
}

const guideControls = byId("guide-controls");
const guideSeconds = byId("guide-seconds");
const guideImage = byId("guide-field-image");
const guideStarImage = byId("guide-star-image");
const guidingPanel = byId("guiding-panel");
const guideRefreshMs = 1000;
let nextGuideRefresh = 0;
let guideBusy = false;
let guideFibreInitialized = false;

function renderGuiding(state) {
  guideControls.hidden = !availableDevices.includes("guiding");
  guideBusy = state.guide_busy === true;
  byId("guide-message").textContent = state.guide_message ?? "";
  byId("guide-snapshot-status").textContent = state.guide_snapshot_message ?? "";
  if (guidingPanel.open && state.guide_frame) {
    const source = `/api/guide-field/${state.guide_frame}`;
    if (guideImage.getAttribute("src") !== source) guideImage.src = source;
  }
  byId("guide-star-status").textContent = state.guide_star_message ?? "Waiting for a guide star…";
  if (!state.guide_star) {
    guideStarImage.hidden = true;
    guideStarImage.removeAttribute("src");
  } else if (guidingPanel.open) {
    const source = `/api/guide-star/${state.guide_star}`;
    if (guideStarImage.getAttribute("src") !== source) guideStarImage.src = source;
  }
  if (guidingPanel.open && !document.hidden && availableDevices.includes("guiding")
      && !guideBusy && !commandPending && Date.now() >= nextGuideRefresh) {
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
guideStarImage.addEventListener("load", () => { guideStarImage.hidden = false; });
guideStarImage.addEventListener("error", () => { guideStarImage.hidden = true; });

for (const button of guideControls.querySelectorAll("[data-guide-action]")) {
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

const calibrationForm = byId("calibration-form");
const calibrationFields = [...calibrationForm.querySelectorAll('input[type="number"]')];
const calibrationName = byId("calibration-name");
const calibrationSave = byId("calibration-save");
const calibrationRun = byId("calibration-run");

function renderCalibration(settings) {
  calibrationForm.hidden = !settings;
  if (!settings || commandPending) return;
  for (const input of calibrationFields) {
    if (!input.dataset.edited && document.activeElement !== input) input.value = settings[input.name];
  }
}

calibrationForm.addEventListener("input", (event) => {
  if (calibrationFields.includes(event.target)) event.target.dataset.edited = "true";
  updateDeviceButtons();
});

calibrationForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const run = event.submitter === calibrationRun;
  if ((run ? calibrationRun : calibrationSave).disabled) return;
  const options = Object.fromEntries(calibrationFields.map((input) => [input.name, input.valueAsNumber]));
  if (run) options.name = calibrationName.value.trim();
  const saved = await sendDeviceCommand("calibration", {
    action: run ? "start_calibration" : "save_settings",
    options,
  }, run ? "calibration-result" : "calibration-settings-result");
  if (saved) {
    for (const input of calibrationFields) delete input.dataset.edited;
  }
});

function updateDeviceButtons() {
  for (const button of guideControls.querySelectorAll("button")) {
    button.disabled = !canCommand("guiding") || guideBusy || batchRunning;
  }
  const telescopeReady = canCommand("telescope") && hardwareState.telescope && !batchRunning;
  byId("slew-start").disabled = !telescopeReady || telescopeSlewActive || acquisitionBusy;
  byId("slew-stop").disabled = !telescopeReady;
  for (const button of lampActions) {
    button.disabled = !canCommand("lamps") || batchRunning || acquisitionBusy;
  }
  const calibrationLocked = !canCommand("calibration") || batchRunning || acquisitionBusy;
  for (const input of calibrationFields) input.disabled = calibrationLocked;
  calibrationSave.disabled = calibrationLocked;
  calibrationRun.disabled = calibrationLocked || hardwareState.camera !== true || !calibrationName.value.trim();
  for (const control of byId("rows").querySelectorAll(".target-control")) {
    control.disabled = !canCommand("batch") || batchRunning || acquisitionBusy;
  }
  const canEditTargets = canCommand("batch") && !batchRunning && !acquisitionBusy;
  for (const handle of byId("rows").querySelectorAll(".target-move-handle")) {
    handle.disabled = !canEditTargets || targetCount < 2 || !targetListRevision || !!activeTargetEditor;
  }
  byId("batch-clear-targets").disabled = !canEditTargets || !targetCount || !targetListRevision || !!activeTargetEditor;
  byId("batch-add-target").disabled = !canEditTargets || !!activeTargetEditor;
  if (activeTargetEditor) {
    for (const control of activeTargetEditor.row.querySelectorAll("input, button")) {
      control.disabled = control.hasAttribute("data-cancel") ? commandPending : !canEditTargets;
    }
  }
  byId("batch-run").disabled = !canEditTargets || telescopeSlewActive || !!activeTargetEditor || !batchRunRevision || enabledTargets === 0;
  byId("batch-load-csv").disabled = !canCommand("batch") || batchRunning || acquisitionBusy;
  byId("batch-download-csv").disabled = !canCommand("batch") || targetCount === 0;
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
  if (telescopeSlewActive) byId("telescope-connection").disabled = true;
  for (const id of ["camera-apply", "camera-cooler-on", "camera-cooler-off"]) {
    byId(id).disabled = !canCommand("camera") || !hardwareState.camera;
  }
  byId("camera-binning").disabled = !canCommand("camera");
  byId("camera-test-start").disabled =
    !canCommand("camera") || !hardwareState.camera || batchRunning || acquisitionBusy || telescopeSlewActive;
  const domeDisabled = !canCommand("dome") || !hardwareState.dome;
  for (const button of domeActions) {
    const positionCommand = ["home", "park"].includes(button.dataset.action);
    button.disabled = domeDisabled || (positionCommand && domeSyncRunning);
  }
  byId("dome-sync").disabled = domeDisabled || (domeSyncRunning && !hardwareState.sync);
}

for (const device of devices) {
  byId(`${device}-toggle`).addEventListener("click", () => toggleHardwarePanel(device));
  byId(`${device}-connection`).addEventListener("click", () => {
    sendDeviceCommand(device, {action: hardwareState[device] ? "disconnect" : "connect"});
  });
}

for (const lamp of lampLabels) {
  byId(`${lamp}-toggle`).addEventListener("click", () => toggleHardwarePanel("lamps"));
}

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
  button.addEventListener("click", () => sendDeviceCommand("lamps", {action: button.dataset.action}));
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
  if (await sendDeviceCommand("camera", {action: "test_image", seconds: exposure.valueAsNumber})) {
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
  if (hardwareState.sync || window.confirm("Enable dome sync? The dome may move.")) {
    sendDeviceCommand("dome", {action: hardwareState.sync ? "sync_off" : "sync_on"});
  }
});

byId("batch-run").addEventListener("click", () => {
  const revision = batchRunRevision;
  if (window.confirm(`Run batch with ${byId("batch-target-count").textContent} in ${byId("mode").textContent.toLowerCase()} mode?`)) {
    sendDeviceCommand("batch", {action: "run", revision});
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

byId("batch-add-target").addEventListener("click", () => openTargetEditor());
byId("batch-clear-targets").addEventListener("click", () => {
  const revision = targetListRevision;
  if (window.confirm(`Clear all ${targetCount} targets from the current batch?`)) {
    sendDeviceCommand("batch", {action: "clear_targets", revision}, "batch-csv-result");
  }
});
const csvPicker = byId("batch-csv-file");
byId("batch-load-csv").addEventListener("click", () => csvPicker.click());
csvPicker.addEventListener("change", async () => {
  const file = csvPicker.files[0];
  csvPicker.value = "";
  if (!file || byId("batch-load-csv").disabled) return;
  if (targetCount && !window.confirm("Replace the current batch target list?")) return;

  let csv;
  try {
    if (file.size > 1024 * 1024) {
      byId("batch-csv-result").textContent = "Choose a CSV smaller than 1 MiB.";
      return;
    }
    csv = new TextDecoder("utf-8", {fatal: true}).decode(await file.arrayBuffer());
  } catch {
    byId("batch-csv-result").textContent = "Could not read the file as UTF-8 CSV.";
    return;
  }
  if (byId("batch-load-csv").disabled) {
    byId("batch-csv-result").textContent = "Cannot load the CSV right now. Try again when the batch is idle.";
    return;
  }
  sendDeviceCommand("batch", {action: "load_csv", csv}, "batch-csv-result");
});
byId("batch-download-csv").addEventListener("click", () => {
  sendDeviceCommand("batch", {action: "download_csv"}, "batch-csv-result");
});

async function sendDeviceCommand(device, command, resultId = `${device}-result`) {
  if (!canCommand(device)) return;
  const result = byId(resultId);
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
    if (command.action === "download_csv" && response.ok) {
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement("a");
      link.href = url;
      link.download = "batch-targets.csv";
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      result.textContent = "";
    } else {
      result.textContent = (await response.json()).message;
    }
    return response.ok;
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
    const watchingGuide = guidingPanel.open && !document.hidden && availableDevices.includes("guiding");
    setTimeout(refresh, watchingGuide ? guideRefreshMs : 2000);
  }
}

refresh();
