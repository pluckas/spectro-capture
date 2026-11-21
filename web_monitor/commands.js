import { byId } from "./dom.js";
export const devices = ["camera", "telescope", "dome"];
export const session = {
  hardwareState: {},
  availableDevices: [],
  commandPending: false,
  statusLive: false,
  batchRunning: false,
  batchStopping: false,
  acquisitionBusy: false,
  telescopeSlewActive: false,
};

let updateControls = () => {};
export function setControlUpdater(callback) {
  updateControls = callback;
}
export function canCommand(device) {
  return session.statusLive && !session.commandPending && session.availableDevices.includes(device) &&
    (!devices.includes(device) || typeof session.hardwareState[device] === "boolean");
}
const statusOnlyActions = {
  camera: ["cooler_on", "cooler_off", "binning", "test_image"],
  dome: ["sync_on", "sync_off", "open", "close", "park", "home", "abort"],
  lamps: ["tungsten", "thorium", "off"],
  telescope: ["slew_name", "slew_coords", "stop_slew"],
  batch: ["run", "stop"],
  calibration: ["save_settings"],
};

export async function sendDeviceCommand(device, command, resultId = `${device}-result`) {
  if (!canCommand(device)) return;
  const result = byId(resultId);
  const connectionAction = command.action === "connect" || command.action === "disconnect";
  const showStatusOnly = (connectionAction && devices.includes(device))
    || statusOnlyActions[device]?.includes(command.action) || device === "guiding";
  result.textContent = showStatusOnly ? "" : "Waiting for Spectro Capture…";
  session.commandPending = true;
  updateControls();
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
      const {message} = await response.json();
      result.textContent = showStatusOnly && response.ok ? "" : message;
    }
    return response.ok;
  } catch {
    result.textContent = "No command result received. Check status before trying again.";
  } finally {
    session.commandPending = false;
    session.statusLive = false;
    updateControls();
  }
}
