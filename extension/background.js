// ScamGuard – Service Worker
// Spricht mit dem lokalen ScamGuard-Server. Content Scripts können das nicht selbst: Anfragen von
// einer Webseite an 127.0.0.1 blockiert der Browser (CORS / Private Network Access), der Service
// Worker darf es dank Host-Berechtigung.

import { ext } from "./lib/ext.js";
import { getSettings } from "./lib/settings.js";

const CLIENT_HEADER = "X-ScamGuard-Client";
const VERSION = ext.runtime.getManifest().version;
const MAX_IMAGES = 4;
const MAX_IMAGE_BYTES = 10 * 1024 * 1024;
// Gleiche Skripte wie im Manifest (eine Quelle) – für Seiten ohne automatischen Scan per executeScript
const CONTENT_SCRIPTS = ext.runtime.getManifest().content_scripts[0].js;
const IMAGE_EXT = { "image/jpeg": "jpg", "image/png": "png", "image/webp": "webp", "image/gif": "gif" };
const BADGE_COLORS = { "hohes Risiko": "#b42318", "verdächtig": "#b54708", "unauffällig": "#067647" };

// --------------------------------------------------------------------------- Hilfsfunktionen

class ScanError extends Error {
  constructor(kind, message) {
    super(message);
    this.kind = kind; // "offline" | "timeout" | "http" | "page"
  }
}

async function fetchWithTimeout(url, options, timeoutMs) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    return await fetch(url, { ...options, signal: ctrl.signal });
  } catch (err) {
    if (err.name === "AbortError") throw new ScanError("timeout", "Zeitüberschreitung beim ScamGuard-Server");
    throw new ScanError("offline", "ScamGuard-Server nicht erreichbar – läuft `scamguard start`?");
  } finally {
    clearTimeout(timer);
  }
}

async function fetchImage(url) {
  if (!/^https:\/\//.test(url)) throw new Error("Nur https-Bilder");
  const resp = await fetch(url, { credentials: "omit" });
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  const blob = await resp.blob();
  if (!IMAGE_EXT[blob.type] || blob.size > MAX_IMAGE_BYTES) throw new Error(`Ungeeignetes Bild (${blob.type})`);
  return blob;
}

// Server-Parameter use_llm je Modus. "auto" lässt den Server entscheiden (nur lokales LLM).
const USE_LLM = { off: "false", auto: "auto", on: "true" };

// Bilder einer Seite werden für Stufe 1, Stufe 2 und das Labeln gebraucht → nur einmal laden.
const IMAGE_CACHE_SIZE = 16;
const imageCache = new Map(); // URL → Blob (älteste zuerst)

async function cachedImage(url) {
  if (!imageCache.has(url)) {
    imageCache.set(url, await fetchImage(url));
    if (imageCache.size > IMAGE_CACHE_SIZE) imageCache.delete(imageCache.keys().next().value);
  }
  return imageCache.get(url);
}

async function listingForm(listing, imageUrls, withImages) {
  const form = new FormData();
  form.append("listing", JSON.stringify(listing));
  // Index (auf der Seite) der Bilder, die tatsächlich hochgeladen wurden – der Server meldet
  // Bildsignale als "image:<Upload-Index>", das Content Script übersetzt zurück.
  const uploadedImageIndices = [];
  if (withImages && imageUrls?.length) {
    const results = await Promise.allSettled(imageUrls.slice(0, MAX_IMAGES).map(cachedImage));
    results.forEach((r, pageIndex) => {
      if (r.status !== "fulfilled") return;
      form.append("images", r.value, `bild_${pageIndex}.${IMAGE_EXT[r.value.type]}`);
      uploadedImageIndices.push(pageIndex);
    });
  }
  return { form, uploadedImageIndices };
}

async function postToServer(path, form, settings, timeoutMs) {
  const resp = await fetchWithTimeout(
    `${settings.apiUrl}${path}`,
    { method: "POST", body: form, headers: { [CLIENT_HEADER]: `extension/${VERSION}` } },
    timeoutMs,
  );
  if (!resp.ok) {
    let detail = `HTTP ${resp.status}`;
    try { detail = (await resp.json()).detail ?? detail; } catch { /* kein JSON */ }
    throw new ScanError("http", `Server-Fehler: ${detail}`);
  }
  return resp.json();
}

async function scanListing(listing, imageUrls, settings, llmMode) {
  const { form, uploadedImageIndices } = await listingForm(listing, imageUrls, settings.analyzeImages);
  form.append("use_llm", USE_LLM[llmMode] ?? "false");
  // lokales LLM: der erste Aufruf lädt das Modell → großzügiges Zeitlimit
  const result = await postToServer("/scan", form, settings, llmMode === "off" ? 30_000 : 150_000);
  return { result, uploadedImageIndices };
}

async function labelListing(listing, imageUrls, label, settings) {
  const { form } = await listingForm(listing, imageUrls, settings.analyzeImages);
  form.append("label", label);
  return postToServer("/label", form, settings, 30_000);
}

async function setBadge(tabId, state) {
  if (tabId == null) return;
  if (state.error) {
    await ext.action.setBadgeText({ tabId, text: "!" });
    await ext.action.setBadgeBackgroundColor({ tabId, color: "#667085" });
    await ext.action.setTitle({ tabId, title: `ScamGuard: ${state.error}` });
    return;
  }
  const pct = Math.round(state.score * 100);
  await ext.action.setBadgeText({ tabId, text: `${pct}%` });
  await ext.action.setBadgeBackgroundColor({ tabId, color: BADGE_COLORS[state.verdict] ?? "#475467" });
  await ext.action.setBadgeTextColor?.({ tabId, color: "#ffffff" });
  await ext.action.setTitle({ tabId, title: `ScamGuard: ${pct} % – ${state.verdict}` });
}

async function rememberResult(tabId, entry) {
  if (tabId == null) return;
  await ext.storage.session.set({ [`tab:${tabId}`]: entry });
}

async function clearTab(tabId) {
  await ext.storage.session.remove(`tab:${tabId}`);
  await ext.action.setBadgeText({ tabId, text: "" }).catch(() => {});
  await ext.action.setTitle({ tabId, title: "ScamGuard" }).catch(() => {});
}

/** Content Scripts in Seiten nachladen, die nicht per Manifest abgedeckt sind (activeTab). */
async function ensureContentScripts(tabId) {
  try {
    const pong = await ext.tabs.sendMessage(tabId, { type: "ping" });
    if (pong?.ok) return;
  } catch { /* noch nicht geladen */ }
  try {
    await ext.scripting.insertCSS({ target: { tabId }, files: ["content/content.css"] });
    await ext.scripting.executeScript({ target: { tabId }, files: CONTENT_SCRIPTS });
  } catch {
    throw new ScanError("page", "Diese Seite kann nicht geprüft werden (z. B. Browser-interne Seiten).");
  }
}

async function scanTab(tabId, mode, text) {
  await ensureContentScripts(tabId);
  return ext.tabs.sendMessage(tabId, { type: "runScan", mode, text });
}

// --------------------------------------------------------------------------- Nachrichten

async function handleMessage(msg, sender) {
  switch (msg?.type) {
    case "getSettings":
      return getSettings();

    case "scan": {
      const tabId = sender.tab?.id;
      const settings = await getSettings();
      try {
        const llmMode = msg.llm ?? "off";
        const { result, uploadedImageIndices } = await scanListing(msg.listing, msg.imageUrls, settings, llmMode);
        await setBadge(tabId, result);
        await rememberResult(tabId, {
          score: result.score,
          verdict: result.verdict,
          signals: result.signals.slice(0, 5).map((s) => ({ message: s.message, weight: s.weight, hard: s.hard })),
          signalCount: result.signals.length,
          source: msg.source,
          url: sender.tab?.url ?? "",
          time: Date.now(),
        });
        return { ok: true, result, uploadedImageIndices };
      } catch (err) {
        const error = { kind: err.kind ?? "unknown", message: err.message };
        await setBadge(tabId, { error: error.message });
        await rememberResult(tabId, { error, url: sender.tab?.url ?? "", time: Date.now() });
        return { ok: false, error };
      }
    }

    case "label": {
      const settings = await getSettings();
      try {
        return { ...(await labelListing(msg.listing, msg.imageUrls, msg.label, settings)), ok: true };
      } catch (err) {
        return { ok: false, error: { kind: err.kind ?? "unknown", message: err.message } };
      }
    }

    case "health": {
      const { apiUrl } = await getSettings();
      try {
        const resp = await fetchWithTimeout(`${apiUrl}/health`, {}, 3_000);
        return resp.ok ? { ok: true, data: await resp.json() } : { ok: false, error: `HTTP ${resp.status}` };
      } catch (err) {
        return { ok: false, error: err.message };
      }
    }

    case "scanTab":
      try {
        return await scanTab(msg.tabId, msg.mode ?? "page", msg.text);
      } catch (err) {
        return { ok: false, error: { kind: err.kind ?? "unknown", message: err.message } };
      }

    default:
      return undefined;
  }
}

ext.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  handleMessage(msg, sender).then(sendResponse, (err) =>
    sendResponse({ ok: false, error: { kind: "unknown", message: String(err) } }));
  return true; // Antwort kommt asynchron
});

// --------------------------------------------------------------------------- Kontextmenü & Tabs

ext.runtime.onInstalled.addListener(async () => {
  await ext.contextMenus.removeAll();
  ext.contextMenus.create({
    id: "scamguard-selection",
    title: "Markierten Text mit ScamGuard prüfen",
    contexts: ["selection"],
  });
  ext.contextMenus.create({
    id: "scamguard-page",
    title: "Diese Seite mit ScamGuard prüfen",
    contexts: ["page"],
  });
});

ext.contextMenus.onClicked.addListener(async (info, tab) => {
  if (!tab?.id) return;
  const selection = info.menuItemId === "scamguard-selection";
  try {
    await scanTab(tab.id, selection ? "selection" : "page", selection ? info.selectionText : undefined);
  } catch (err) {
    await setBadge(tab.id, { error: err.message });
  }
});

ext.tabs.onUpdated.addListener((tabId, changeInfo) => {
  if (changeInfo.status === "loading" && changeInfo.url) clearTab(tabId);
});

ext.tabs.onRemoved.addListener((tabId) => {
  ext.storage.session.remove(`tab:${tabId}`);
});
