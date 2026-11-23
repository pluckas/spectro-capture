import "./navigation.js";
import { byId, clock } from "./dom.js";
import { session, setControlUpdater } from "./commands.js";
import { renderBatch, updateBatchControls } from "./batch.js";
import { renderCalibration, updateCalibrationControls } from "./calibration.js";
import { guideRefreshMs, renderGuiding, updateGuidingControls, watchingGuide } from "./guiding.js";
import { renderHardware, updateHardwareControls } from "./hardware.js";
import { renderSystemStatus } from "./system-status.js";

function updateDeviceButtons() {
  updateHardwareControls();
  updateBatchControls();
  updateCalibrationControls();
  updateGuidingControls();
}

setControlUpdater(updateDeviceButtons);

function render(state) {
  session.batchRunning = state.batch_running === true;
  session.batchStopping = state.batch_stopping === true;
  session.acquisitionBusy = state.acquisition_busy === true;
  session.telescopeSlewActive = state.telescope_slew_active === true;
  renderHardware(state);
  renderGuiding(state);
  renderCalibration(state.calibration_settings);
  renderSystemStatus(state, renderBatch(state));
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
    const statusAge = Date.parse(state.server_time) - Date.parse(lastUpdate);
    const stale = !Number.isFinite(statusAge) || statusAge < 0 || statusAge > 10_000;
    session.statusLive = !stale;
    byId("connection").textContent = stale
      ? `Waiting for desktop refresh · ${clock(lastUpdate)}`
      : `${state.view_only ? "View only" : session.availableDevices.length ? "Controls enabled" : "Read-only"} · Updated ${clock(lastUpdate)}`;
  } catch {
    session.statusLive = false;
    byId("connection").textContent = `Connection lost · Last update ${clock(lastUpdate)}`;
  } finally {
    updateDeviceButtons();
    setTimeout(refresh, watchingGuide() ? guideRefreshMs : 2000);
  }
}

refresh();
