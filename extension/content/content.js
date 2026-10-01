// ScamGuard – Ablaufsteuerung im Tab: auslesen → Service Worker → markieren → Panel.

(() => {
  const SG = (globalThis.ScamGuard ??= {});
  if (SG.loaded) return; // Mehrfach-Injektion (Manifest + Popup) abfangen
  SG.loaded = true;
  const ext = globalThis.browser ?? globalThis.chrome; // Firefox: browser.*, Chromium: chrome.*

  const MIN_NEEDLE_LENGTH = 3;

  /** Seitenelement zu einem Signal-Target ("price", "seller", "image:<Upload-Index>"). */
  function resolveTarget(target, extracted, uploadedImageIndices) {
    if (!target) return null;
    if (target === "price") return extracted.elements.price ?? null;
    if (target === "seller") return extracted.elements.seller ?? null;
    const m = /^image:(\d+)$/.exec(target);
    if (m) {
      const pageIndex = uploadedImageIndices?.[Number(m[1])];
      return pageIndex == null ? null : extracted.elements.images[pageIndex] ?? null;
    }
    return null;
  }

  /** Setzt alle Markierungen. Rückgabe: Menge der Signal-IDs, die sichtbar markiert wurden. */
  function applyMarks(signals, extracted, uploadedImageIndices) {
    SG.highlight.begin(signals);
    const marked = new Set();

    // Gleiche Fundstelle mehrerer Signale nur einmal markieren; lange Stellen zuerst,
    // damit kürzere darin enthaltene Stellen der längeren Markierung zugeordnet werden.
    const byNeedle = new Map();
    for (const s of signals) {
      for (const needle of s.highlights ?? []) {
        const key = needle.trim().toLowerCase().replace(/\s+/g, " ");
        if (key.length < MIN_NEEDLE_LENGTH) continue;
        if (!byNeedle.has(key)) byNeedle.set(key, { needle, signals: [] });
        byNeedle.get(key).signals.push(s);
      }
    }
    const entries = [...byNeedle.values()].sort((a, b) => b.needle.length - a.needle.length);
    for (const { needle, signals: group } of entries) {
      if (SG.highlight.markText(extracted.roots, needle, group) > 0) group.forEach((s) => marked.add(s.id));
    }

    for (const s of signals) {
      const el = resolveTarget(s.target, extracted, uploadedImageIndices);
      if (el && SG.highlight.markElement(el, [s])) marked.add(s.id);
    }
    return marked;
  }

  let currentRun = 0; // ein neuer Scan (z. B. „Erneut prüfen“) verwirft ältere, noch laufende Antworten

  async function requestScan(extracted, llm) {
    try {
      return await ext.runtime.sendMessage({
        type: "scan",
        listing: extracted.listing,
        imageUrls: extracted.imageUrls,
        source: extracted.source,
        llm,
      });
    } catch (err) {
      return { ok: false, error: { kind: "extension", message: `Extension-Fehler: ${err.message}` } };
    }
  }

  /** Eigene Einstufung → Trainingsdaten. Die URL ist der Schlüssel: erneutes Labeln derselben
   *  Anzeige ersetzt das alte Label. Markierter Text hat keine eigene URL → dort zählt der Text. */
  async function sendLabel(extracted, label) {
    const listing = { ...extracted.listing };
    if (extracted.source !== "selection") listing.url = location.origin + location.pathname;
    try {
      return await ext.runtime.sendMessage({ type: "label", listing, imageUrls: extracted.imageUrls, label });
    } catch (err) {
      return { ok: false, error: { kind: "extension", message: `Extension-Fehler: ${err.message}` } };
    }
  }

  function show(settings, response, extracted, handlers) {
    const { result, uploadedImageIndices } = response;
    const marked = applyMarks(result.signals, extracted, uploadedImageIndices);
    if (settings.showPanel) SG.panel.showResult(result, marked, handlers);
    return marked;
  }

  /** Zweistufig: erst die schnellen Detektoren anzeigen, dann (falls gewünscht) die KI-Analyse
   *  nachreichen – ein lokales LLM braucht auf einem Laptop schnell 10–30 Sekunden. */
  async function runScan(mode = "auto", text = "") {
    const run = ++currentRun;
    const superseded = { ok: false, error: { kind: "superseded", message: "durch neueren Scan ersetzt" } };
    const settings = await ext.runtime.sendMessage({ type: "getSettings" });
    const extracted = SG.extract.forMode(mode, text);
    const wantsLlm = settings.llmMode !== "off";
    const handlers = {
      onFocus: (id) => SG.highlight.focus(id),
      onRescan: () => runScan(mode, text),
      onLabel: (label) => sendLabel(extracted, label),
    };
    if (settings.showPanel) SG.panel.showLoading(extracted.source);

    // Stufe 1: Regeln, Textmodell, Bilder
    const fast = await requestScan(extracted, "off");
    if (run !== currentRun) return superseded;
    if (!fast?.ok) {
      SG.highlight.clearAll();
      if (settings.showPanel) SG.panel.showError(fast?.error, handlers.onRescan);
      return { ok: false, error: fast?.error };
    }
    let marked = show(settings, fast, extracted, { ...handlers, aiPending: wantsLlm });
    let final = fast.result;

    // Stufe 2: KI-Analyse (welches LLM, entscheidet der Server)
    if (wantsLlm) {
      const full = await requestScan(extracted, settings.llmMode);
      if (run !== currentRun) return superseded;
      if (full?.ok) {
        marked = show(settings, full, extracted, handlers);
        final = full.result;
      } else if (settings.showPanel) {
        SG.panel.showResult(fast.result, marked, { ...handlers, aiError: full?.error?.message });
      }
    }
    return { ok: true, score: final.score, verdict: final.verdict, marked: marked.size };
  }

  ext.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
    if (msg?.type === "ping") {
      sendResponse({ ok: true });
      return false;
    }
    if (msg?.type === "runScan") {
      runScan(msg.mode, msg.text).then(sendResponse, (err) =>
        sendResponse({ ok: false, error: { kind: "extension", message: String(err) } }));
      return true;
    }
    return false;
  });

  // Plattform-Seiten (per Manifest geladen) automatisch prüfen
  ext.runtime.sendMessage({ type: "getSettings" }).then((settings) => {
    if (settings?.autoScan && SG.extract.isListingPage()) runScan("auto");
  });
})();
