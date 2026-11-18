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
    ["Camera", connected(h.camera)], ["Telescope", connected(h.telescope)], ["Dome", connected(h.dome)],
    ["Dome sync", h.slewing ? "Dome moving" : h.dome ? onOff(h.sync) : "—"],
    ["Cooler", h.cooler && h.setpoint != null ? `On · set ${h.setpoint} °C` : onOff(h.cooler)],
    ["CCD", h.temperature == null ? "—" : `${h.temperature.toFixed(1)} °C`],
    ["Binning", h.binning ?? "—"], ["Tungsten", onOff(h.tungsten)], ["Thorium", onOff(h.thorium)],
  ];
  byId("hardware").replaceChildren(...status.map(([label, value]) => {
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
    byId("connection").textContent = stale
      ? `Waiting for desktop refresh · ${clock(lastUpdate)}`
      : `Read-only · Updated ${clock(lastUpdate)}`;
  } catch {
    byId("connection").textContent = `Connection lost · Last update ${clock(lastUpdate)}`;
  } finally {
    setTimeout(refresh, 2000);
  }
}

refresh();
