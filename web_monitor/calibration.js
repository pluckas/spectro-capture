import { byId } from "./dom.js";
import { session, canCommand, sendDeviceCommand } from "./commands.js";

const calibrationForm = byId("calibration-form");
const calibrationFields = [...calibrationForm.querySelectorAll('input[type="number"]')];
const calibrationName = byId("calibration-name");
const calibrationSave = byId("calibration-save");
const calibrationRun = byId("calibration-run");

export function renderCalibration(settings) {
  calibrationForm.hidden = !settings;
  const available = session.availableDevices.includes("calibration");
  byId("calibration-apply-controls").hidden = !available;
  byId("calibration-run-controls").hidden = !available;
  if (!settings || session.commandPending) return;
  for (const input of calibrationFields) {
    if (!input.dataset.edited && document.activeElement !== input) input.value = settings[input.name];
  }
}

calibrationForm.addEventListener("input", (event) => {
  if (calibrationFields.includes(event.target)) event.target.dataset.edited = "true";
  updateCalibrationControls();
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

export function updateCalibrationControls() {
  const calibrationLocked = !canCommand("calibration") || session.batchRunning || session.acquisitionBusy;
  for (const input of calibrationFields) input.disabled = calibrationLocked;
  calibrationSave.disabled = calibrationLocked;
  calibrationRun.disabled = calibrationLocked || session.hardwareState.camera !== true || !calibrationName.value.trim();
}
