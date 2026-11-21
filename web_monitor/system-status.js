import { byId, clock, element } from "./dom.js";
export const statusDetails = byId("status-details");
const statusDetailsToggle = byId("status-details-toggle");
statusDetailsToggle.addEventListener("click", () => {
  statusDetails.hidden = !statusDetails.hidden;
  byId(statusDetails.hidden ? "status-acquisition-compact" : "status-camera").append(byId("status-exposure"));
  statusDetailsToggle.textContent = statusDetails.hidden ? "Show Details" : "Hide Details";
  statusDetailsToggle.setAttribute("aria-expanded", String(!statusDetails.hidden));
});

const runLog = byId("recent-panel");
const runLogToggle = byId("status-log-toggle");
runLogToggle.addEventListener("click", () => { runLog.open = !runLog.open; });
runLog.addEventListener("toggle", () => {
  runLogToggle.textContent = runLog.open ? "Hide Run Log" : "Show Run Log";
  runLogToggle.setAttribute("aria-expanded", String(runLog.open));
});

function sexagesimal(value, places, signed, degrees = false) {
  if (value == null) return "—";
  const scale = 10 ** places;
  const ticks = Math.round(Math.abs(value) * 3600 * scale);
  const leading = String(Math.floor(ticks / (3600 * scale))).padStart(2, "0");
  const minutes = String(Math.floor(ticks / (60 * scale)) % 60).padStart(2, "0");
  const seconds = ((ticks / scale) % 60).toFixed(places).padStart(places ? places + 3 : 2, "0");
  const sign = value < 0 ? "−" : signed ? "+" : "";
  return degrees ? `${sign}${leading}°${minutes}′${seconds}″` : `${sign}${leading}:${minutes}:${seconds}`;
}

function renderPointingAndWeather(state) {
  const pointing = state.pointing ?? {};
  byId("observatory-local").textContent = state.local_time ?? "—";
  byId("observatory-utc").textContent = state.utc_time ?? "—";
  byId("observatory-lst").textContent = sexagesimal(pointing.lst, 0, false);
  for (const field of ["alt", "az"]) {
    byId(`point-${field}`).textContent = pointing[field] == null ? "—" : `${pointing[field].toFixed(2)}°`;
  }
  byId("point-ha").textContent = sexagesimal(pointing.ha, 0, true);
  byId("point-ra").textContent = sexagesimal(pointing.ra, 2, false);
  byId("point-dec").textContent = sexagesimal(pointing.dec, 1, true, true);
  const weather = state.weather ?? {};
  const temperature = (value) => value == null ? null : `${value.toFixed(1)} °C`;
  const percent = (value) => value == null ? null : `${value}%`;
  const skyDelta = temperature(weather.sky_delta);
  byId("status-sky-condition").textContent = weather.sky_condition ?? weather.message ?? "—";
  byId("status-weather").textContent = skyDelta ?? "—";
  const message = byId("weather-message");
  message.textContent = weather.message ?? "";
  message.hidden = !weather.message;
  byId("weather-readings").hidden = Boolean(weather.message);
  const wind = weather.wind == null ? null : `${weather.wind.toFixed(1)} ${weather.wind_unit}`;
  const readings = {
    "sky-condition": weather.sky_condition,
    "sky-delta": skyDelta,
    outside: temperature(weather.outside),
    "dew-point": temperature(weather.dew_point),
    humidity: percent(weather.humidity),
    wind: [wind, weather.wind_condition].filter(Boolean).join(" · ") || null,
    rain: weather.rain,
    light: weather.light,
    sensor: temperature(weather.sensor),
    heater: percent(weather.heater),
  };
  for (const [field, value] of Object.entries(readings)) {
    byId(`weather-${field}`).textContent = value ?? "—";
  }
}

let lastLogKey;
function renderRunLog(events) {
  if (!runLog.open) return;
  const key = JSON.stringify(events);
  if (key === lastLogKey) return;
  lastLogKey = key;
  const list = byId("events");
  const previousFirst = list.firstElementChild?.dataset.eventKey;
  const previousTop = list.scrollTop;
  list.replaceChildren(...[...events].reverse().map((event) => {
    const item = document.createElement("li");
    item.dataset.eventKey = JSON.stringify(event);
    const time = element("time", clock(event.time));
    time.dateTime = event.time;
    item.append(time, document.createTextNode(event.message));
    return item;
  }));
  const formerFirst = [...list.children].find((item) => item.dataset.eventKey === previousFirst);
  if (previousTop > 0 && formerFirst) {
    list.scrollTop = previousTop + formerFirst.offsetTop - list.firstElementChild.offsetTop;
  }
}
export function renderSystemStatus(state, {activity, exposure, exposureText, activeTarget}) {
  renderPointingAndWeather(state);
  byId("status-acquisition").textContent = exposure
    ? [activeTarget !== exposure.label ? activeTarget : null, exposureText].filter(Boolean).join(" · ")
    : activity;
  const progress = byId("status-progress");
  progress.hidden = !exposure;
  if (exposure) {
    progress.max = Math.max(1, exposure.seconds);
    progress.value = exposure.elapsed;
  }
  renderRunLog(state.events);
}
