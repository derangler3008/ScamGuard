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

  async function runScan(mode = "auto", text = "") {
    const settings = await ext.runtime.sendMessage({ type: "getSettings" });
    const extracted = SG.extract.forMode(mode, text);
    const rescan = () => runScan(mode, text);
    if (settings.showPanel) SG.panel.showLoading(extracted.source);

    let response;
    try {
      response = await ext.runtime.sendMessage({
        type: "scan",
        listing: extracted.listing,
        imageUrls: extracted.imageUrls,
        source: extracted.source,
      });
    } catch (err) {
      response = { ok: false, error: { kind: "extension", message: `Extension-Fehler: ${err.message}` } };
    }

    if (!response?.ok) {
      SG.highlight.clearAll();
      if (settings.showPanel) SG.panel.showError(response?.error, rescan);
      return { ok: false, error: response?.error };
    }

    const { result, uploadedImageIndices } = response;
    const marked = applyMarks(result.signals, extracted, uploadedImageIndices);
    if (settings.showPanel) {
      SG.panel.showResult(result, marked, { onFocus: (id) => SG.highlight.focus(id), onRescan: rescan });
    }
    return { ok: true, score: result.score, verdict: result.verdict, marked: marked.size };
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
