export const byId = (id) => document.getElementById(id);
export const clock = (value) => value ? new Date(value).toISOString().slice(11, 19) + " UT" : "—";
export const connected = (value) => value == null ? "—" : value ? "Connected" : "Disconnected";
export const onOff = (value) => value == null ? "—" : value ? "On" : "Off";
export function element(tag, value) {
  const node = document.createElement(tag);
  node.textContent = value;
  return node;
}
