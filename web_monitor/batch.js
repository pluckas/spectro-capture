import { byId, element } from "./dom.js";
import { session, canCommand, sendDeviceCommand } from "./commands.js";

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
  if (document.activeElement !== start) start.value = batchSchedule.start_local;
}

let targetTableKey;
let activeTargetEditor;
let targetDrag;

function renderTargets(rows, run, edits) {
  const editable = session.availableDevices.includes("batch") && !session.batchRunning && !run?.running;
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
      table.prepend(activeTargetEditor.row);
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
  if (edit.id) form.querySelectorAll("[data-quick-target]").forEach((label) => label.remove());
  const values = reference ?? {name, ra: edit.ra ?? "", dec: edit.dec ?? ""};
  for (const [field, value] of Object.entries(values)) {
    form.elements.namedItem(field).value = value;
  }
  activeTargetEditor = {row, edit};
  if (targetRow) targetRow.after(row);
  else byId("rows").prepend(row);
  const cancel = () => {
    closeTargetEditor();
    updateBatchControls();
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
    if (event.key === "Escape" && !session.commandPending) {
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
  updateBatchControls();
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

export function renderBatch(state) {
  const run = state.run;
  batchSchedule = state.batch_schedule;
  byId("batch-options").hidden = !batchSchedule;
  if (batchSchedule && !session.commandPending) renderSchedule();
  targetCount = state.plan.length;
  targetListRevision = state.target_list_revision;
  batchRunRevision = state.batch_run_revision;
  byId("batch-csv-controls").hidden = !session.availableDevices.includes("batch");
  enabledTargets = state.plan.filter((row) => row.enabled).length;
  const exposure = session.batchStopping ? null : run?.exposure;
  const mode = session.batchRunning && run?.running ? run.mode : state.plan_mode ?? "";
  byId("mode").textContent = mode;
  const activity = session.batchStopping ? "Stopping…" :
    session.batchRunning ? (run?.running ? run.activity : "Finishing batch…") : "Idle";
  byId("batch-commands").hidden = !session.availableDevices.includes("batch");
  byId("batch-target-count").textContent = `${enabledTargets} enabled ${enabledTargets === 1 ? "target" : "targets"}`;

  // Retain the completed run until the desktop plan changes.
  const samePlan = run && state.plan.length === run.rows.length &&
    state.plan.every((row, i) => Object.keys(row).every((key) => row[key] === run.rows[i][key]));
  const rows = run?.running || samePlan ? run.rows : state.plan;
  byId("targets").textContent = run?.running ? "Active batch targets" : "Targets";
  renderTargets(rows, run, state.target_edits ?? []);
  const active = run?.running ? run.active : null;
  const row = active == null ? null : run.rows[active];
  const position = row ? run.target_number : null;
  const total = run?.rows.filter((target) => target.enabled).length;
  const reference = run?.activity?.startsWith("Observing reference ");
  return {activity, exposure, target: reference ? row?.reference : row?.target, position, total, reference};
}

export function updateBatchControls() {
  const canEditTargets = canCommand("batch") && !session.batchRunning && !session.acquisitionBusy;
  for (const control of byId("rows").querySelectorAll(".target-control")) {
    control.disabled = !canEditTargets;
  }
  for (const handle of byId("rows").querySelectorAll(".target-move-handle")) {
    handle.disabled = !canEditTargets || targetCount < 2 || !targetListRevision || !!activeTargetEditor;
  }
  byId("batch-clear-targets").disabled = !canEditTargets || !targetCount || !targetListRevision || !!activeTargetEditor;
  byId("batch-add-target").disabled = !canEditTargets || !!activeTargetEditor;
  if (activeTargetEditor) {
    for (const control of activeTargetEditor.row.querySelectorAll("input, button")) {
      control.disabled = control.hasAttribute("data-cancel") ? session.commandPending : !canEditTargets;
    }
  }
  byId("batch-run").disabled = !canEditTargets || session.telescopeSlewActive || !!activeTargetEditor || !batchRunRevision || enabledTargets === 0;
  byId("batch-load-csv").disabled = !canCommand("batch") || session.batchRunning || session.acquisitionBusy;
  byId("batch-download-csv").disabled = !canCommand("batch") || targetCount === 0;
  byId("batch-stop").disabled = !canCommand("batch") || !session.batchRunning || session.batchStopping;
  const scheduleLocked = !batchSchedule || !canCommand("batch") || session.batchRunning || session.acquisitionBusy;
  for (const [id, , requiresScheduling] of batchOptionCheckboxes) {
    byId(id).disabled = scheduleLocked || (requiresScheduling && !batchSchedule.enabled);
  }
  for (const id of ["schedule-ha-min", "schedule-ha-max", "schedule-start"]) {
    byId(id).disabled = scheduleLocked || !batchSchedule.enabled;
  }
}

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
  if (property !== "checked") {
    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter") input.blur();
    });
  }
}

byId("batch-add-target").addEventListener("click", () => {
  byId("targets-panel").open = true;
  openTargetEditor();
});
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
  const confirmLoad = activeTargetEditor
    ? "Discard the unsaved target edit and load the CSV?"
    : "Replace the current batch target list?";
  if ((targetCount || activeTargetEditor) && !window.confirm(confirmLoad)) return;

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
  if (await sendDeviceCommand("batch", {action: "load_csv", csv}, "batch-csv-result")) {
    closeTargetEditor();
    updateBatchControls();
  }
});
byId("batch-download-csv").addEventListener("click", () => {
  sendDeviceCommand("batch", {action: "download_csv"}, "batch-csv-result");
});
