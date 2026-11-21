import { byId } from "./dom.js";
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
    if (panel.parentElement === workspacePanels && workspacePanels.firstElementChild !== panel) {
      workspacePanels.prepend(panel);
    }
    panel.open = true;
    const summary = panel.querySelector("summary");
    summary.focus({preventScroll: true});
    summary.scrollIntoView({block: "nearest"});
  });
}
