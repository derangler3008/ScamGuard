// Gemeinsame Einstellungen für Service Worker und Popup (Content Scripts fragen den Service Worker).

import { ext } from "./ext.js";

export const DEFAULT_SETTINGS = Object.freeze({
  apiUrl: "http://127.0.0.1:8000", // lokaler ScamGuard-Server (`scamguard api`)
  autoScan: true,                  // Kleinanzeigen-Anzeigen beim Öffnen automatisch prüfen
  analyzeImages: true,             // Inseratsbilder mitschicken (Bildmodelle)
  useLlm: false,                   // Claude zuschalten – kostet API-Guthaben, Text geht an Anthropic
  showPanel: true,                 // Ergebnis-Panel auf der Seite einblenden
});

// Nur lokale Server: Dafür hat die Extension Host-Berechtigungen, und Inseratsdaten bleiben auf dem Rechner.
export const API_URL_PATTERN = /^http:\/\/(127\.0\.0\.1|localhost)(:\d{1,5})?$/;

export async function getSettings() {
  const stored = await ext.storage.sync.get(DEFAULT_SETTINGS);
  const settings = { ...DEFAULT_SETTINGS, ...stored };
  if (!API_URL_PATTERN.test(settings.apiUrl)) settings.apiUrl = DEFAULT_SETTINGS.apiUrl;
  return settings;
}

export async function saveSettings(partial) {
  await ext.storage.sync.set(partial);
}
