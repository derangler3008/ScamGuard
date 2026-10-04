import { ext } from "../lib/ext.js";
import { API_URL_PATTERN, getSettings, saveSettings } from "../lib/settings.js";

const VERDICTS = {
  "hohes Risiko": { cls: "high", icon: "danger", label: "Hohes Risiko" },
  "verdächtig": { cls: "mid", icon: "warning", label: "Verdächtig" },
  "unauffällig": { cls: "ok", icon: "ok", label: "Unauffällig" },
};
const TOGGLES = ["autoScan", "analyzeImages", "showPanel"];
const $ = (id) => document.getElementById(id);

function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text != null) node.textContent = text;
  return node;
}

async function activeTab() {
  const [tab] = await ext.tabs.query({ active: true, currentWindow: true });
  return tab;
}

const LISTING_PAGE = /^https:\/\/www\.kleinanzeigen\.de\/(s-anzeige\/|m-nachrichten)/;

function renderResult(entry, tabUrl) {
  const box = $("result");
  box.className = "";
  box.replaceChildren();
  if (!entry) {
    box.append(el("p", "muted", LISTING_PAGE.test(tabUrl ?? "")
      ? "Dieser Tab wurde noch nicht geprüft."
      : "Öffne eine einzelne Anzeige oder dein Postfach auf kleinanzeigen.de – dort prüft ScamGuard " +
        "automatisch und markiert Warnsignale. Andere Seiten: „Diese Seite prüfen“."));
    return;
  }
  if (entry.error) {
    box.append(el("p", "error", entry.error.message));
    return;
  }
  const v = VERDICTS[entry.verdict] ?? { cls: "", icon: null, label: entry.verdict };
  box.className = v.cls;
  const score = el("p", "score");
  const verdict = el("span", "verdict");
  // icons.js ist ein klassisches Skript (auch für die Content Scripts) und hängt an globalThis.ScamGuard
  if (v.icon) verdict.append(globalThis.ScamGuard.icon(v.icon, { size: 18 }));
  verdict.append(v.label);
  score.append(el("span", "pct", `${Math.round(entry.score * 100)} %`), verdict);
  box.append(score);
  if (!entry.signals?.length) {
    box.append(el("p", "muted", "Keine Warnsignale gefunden."));
    return;
  }
  const list = el("ul");
  for (const s of entry.signals.slice(0, 3)) list.append(el("li", "", s.message));
  box.append(list);
  if (entry.signalCount > 3) {
    box.append(el("p", "muted", `… und ${entry.signalCount - 3} weitere – Details im Panel auf der Seite.`));
  }
}

async function refreshResult(tab) {
  const key = `tab:${tab.id}`;
  const stored = await ext.storage.session.get(key);
  renderResult(stored[key], tab.url);
}

async function checkServer() {
  const status = $("server-status");
  status.className = "status";
  status.textContent = "Server …";
  const health = await ext.runtime.sendMessage({ type: "health" });
  status.classList.add(health?.ok ? "online" : "offline");
  status.textContent = health?.ok ? "Server verbunden" : "Server aus";
  status.title = health?.ok ? `ScamGuard ${health.data.version}` : health?.error ?? "";
  const labels = health?.data?.labels;
  $("label-info").textContent = labels
    ? `Eigene Labels: ${labels.gesamt} (${labels.betrug} Betrug, ${labels.serioes} seriös) – einstufen im Panel auf der Seite`
    : "";
  const llm = health?.data?.llm;
  $("llm-info").textContent = !llm
    ? "Modell: unbekannt (Server aus)"
    : llm.provider === "local"
      ? `Modell: ${llm.model.split("/").pop()} – lokal, kostenlos, Daten bleiben auf dem Rechner`
      : `Modell: ${llm.model} (Claude API) – kostet pro Prüfung, Text geht an Anthropic`;
}

async function initSettings() {
  const settings = await getSettings();
  for (const key of TOGGLES) {
    const box = $(key);
    box.checked = Boolean(settings[key]);
    box.addEventListener("change", () => saveSettings({ [key]: box.checked }));
  }
  const llmMode = $("llmMode");
  llmMode.value = settings.llmMode;
  llmMode.addEventListener("change", () => saveSettings({ llmMode: llmMode.value }));

  const url = $("apiUrl");
  url.value = settings.apiUrl;
  url.addEventListener("change", async () => {
    const value = url.value.trim().replace(/\/+$/, "");
    if (!API_URL_PATTERN.test(value)) {
      $("apiUrl-error").textContent = "Nur lokale Adressen, z. B. http://127.0.0.1:8000";
      url.setAttribute("aria-invalid", "true");
      return;
    }
    $("apiUrl-error").textContent = "";
    url.removeAttribute("aria-invalid");
    url.value = value;
    await saveSettings({ apiUrl: value });
    checkServer();
  });
}

async function initScanButton(tab) {
  const button = $("scan-btn");
  if (!tab?.id || !/^https?:/.test(tab.url ?? "")) {
    button.disabled = true;
    button.textContent = "Diese Seite kann nicht geprüft werden";
    return;
  }
  button.addEventListener("click", async () => {
    button.disabled = true;
    button.textContent = "Prüfe …";
    const response = await ext.runtime.sendMessage({ type: "scanTab", tabId: tab.id });
    button.disabled = false;
    button.textContent = "Erneut prüfen";
    // Server-Fehler stehen bereits im Tab-Ergebnis; Fehler vor dem Scan (z. B. Browser-interne
    // Seite) nicht – die direkt anzeigen.
    if (response?.ok === false && SCAN_NOT_STARTED.has(response.error?.kind)) {
      renderResult({ error: response.error });
      return;
    }
    await refreshResult(tab);
  });
}

const SCAN_NOT_STARTED = new Set(["page", "extension", "unknown"]);
const REQUIRED_ORIGINS = ext.runtime.getManifest().host_permissions;

async function checkPermissions() {
  const granted = await ext.permissions.contains({ origins: REQUIRED_ORIGINS });
  $("permissions").hidden = granted;
  return granted;
}

$("grant-btn").addEventListener("click", () => {
  // Ohne await davor: Firefox erlaubt permissions.request nur direkt in der Nutzeraktion
  ext.permissions.request({ origins: REQUIRED_ORIGINS }).then(() => {
    checkPermissions();
    checkServer();
  });
});

const tab = await activeTab();
checkPermissions();
initSettings();
checkServer();
initScanButton(tab);
if (tab?.id) {
  refreshResult(tab);
  // Ergebnis live aktualisieren, falls der automatische Scan gerade fertig wird
  ext.storage.onChanged.addListener((changes, area) => {
    if (area === "session" && changes[`tab:${tab.id}`]) refreshResult(tab);
  });
}
