// Gemeinsame Einstellungen für Service Worker und Popup (Content Scripts fragen den Service Worker).

import { ext } from "./ext.js";

export const DEFAULT_SETTINGS = Object.freeze({
  apiUrl: "http://127.0.0.1:8000", // lokaler ScamGuard-Server (`scamguard start`)
  autoScan: true,                  // Kleinanzeigen-Anzeigen beim Öffnen automatisch prüfen
  analyzeImages: true,             // Inseratsbilder mitschicken (Bildmodelle)
  // KI-Analyse per LLM: "auto" = nur ein lokales Modell (kostenlos, Daten bleiben auf dem Rechner),
  // "on" = immer (auch Claude, kostet), "off" = nie. Welches LLM, legt der Server fest (config.yaml).
  llmMode: "auto",
  showPanel: true,                 // Ergebnis-Panel auf der Seite einblenden
});

// Nur lokale Server: Dafür hat die Extension Host-Berechtigungen, und Inseratsdaten bleiben auf dem Rechner.
export const LLM_MODES = ["auto", "on", "off"];

export const API_URL_PATTERN = /^http:\/\/(127\.0\.0\.1|localhost)(:\d{1,5})?$/;

export async function getSettings() {
  const stored = await ext.storage.sync.get(DEFAULT_SETTINGS);
  const settings = { ...DEFAULT_SETTINGS, ...stored };
  if (!LLM_MODES.includes(settings.llmMode)) settings.llmMode = DEFAULT_SETTINGS.llmMode;
  if (!API_URL_PATTERN.test(settings.apiUrl)) settings.apiUrl = DEFAULT_SETTINGS.apiUrl;
  return settings;
}

export async function saveSettings(partial) {
  await ext.storage.sync.set(partial);
}
