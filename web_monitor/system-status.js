import { byId, clock, element } from "./dom.js";
const runLog = byId("recent-panel");

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
const lastImage = byId("last-image");
const thumbnail = byId("last-image-thumbnail");
thumbnail.addEventListener("load", () => { lastImage.hidden = false; });
thumbnail.addEventListener("error", () => {
  lastImage.hidden = true;
  thumbnail.removeAttribute("src");
});

export function renderSystemStatus(state, {activity, exposure, target, position, total, reference}) {
  renderPointingAndWeather(state);
  const test = state.test_exposure;
  const label = exposure?.label;
  const kind = label === "Tungsten" || label === "Thorium" ? label : reference ? "Reference" : "Science";
  byId("status-target").textContent = test ? "Test exposure" : target ?? "";
  byId("status-acquisition").textContent = test ? "Preview only" : exposure
    ? [kind, position == null ? null : `Target ${position} of ${total}`, `Frame ${exposure.index} of ${exposure.total}`].filter(Boolean).join(" · ")
    : activity;
  const progress = byId("status-progress");
  const elapsed = test ? Math.max(0, (Date.parse(state.server_time) - Date.parse(test.started)) / 1000) : exposure?.elapsed;
  const duration = test?.seconds ?? exposure?.seconds;
  progress.hidden = duration == null;
  if (duration != null) {
    progress.max = Math.max(1, duration);
    progress.value = Math.min(elapsed, duration);
  }
  byId("status-exposure-time").textContent = duration == null ? "" :
    test && elapsed >= duration ? "Reading image…" : `${Math.floor(elapsed)} / ${duration} s`;
  if (state.viewer) {
    const source = `/api/thumbnail/${state.viewer.version}`;
    if (thumbnail.getAttribute("src") !== source) {
      lastImage.hidden = true;
      thumbnail.src = source;
      byId("last-image-label").textContent = state.viewer.label ?? "";
    }
  }
  renderRunLog(state.events);
}
