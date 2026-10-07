// ScamGuard – Ergebnis-Panel auf der Seite (Shadow DOM: Seite und Panel beeinflussen sich nicht).
// Sicherheit: Texte aus dem Inserat werden nur über textContent/append(string) eingefügt, nie als HTML.

(() => {
  const SG = (globalThis.ScamGuard ??= {});
  if (SG.panel) return;

  const VERDICTS = {
    "hohes Risiko": { cls: "high", icon: "danger", label: "Hohes Risiko" },
    "verdächtig": { cls: "mid", icon: "warning", label: "Verdächtig" },
    "unauffällig": { cls: "ok", icon: "ok", label: "Unauffällig" },
  };
  // Schwere nicht nur über Farbe: Dreieck / Raute / Kreis (CSS-Formen, siehe .sev)
  const SEVERITY = { high: "Hoch", mid: "Mittel", low: "Niedrig" };
  const MODEL_NAMES = { rules: "Regeln", text_model: "Text", image_model: "Bild", llm: "LLM" };
  const MAX_VISIBLE_SIGNALS = 6;

  const CSS = `
    .card { font: 13px/1.45 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; color: #101828;
      background: #ffffff; border: 1px solid #d0d5dd; border-radius: 12px; width: 340px;
      max-height: calc(100vh - 120px); overflow: auto; box-shadow: 0 12px 32px rgba(16, 24, 40, .18); }
    .head { display: flex; align-items: center; gap: 6px; padding: 8px 10px 8px 12px; position: sticky; top: 0;
      background: inherit; border-bottom: 1px solid #eaecf0; border-radius: 12px 12px 0 0; }
    .head strong { font-size: 14px; }
    .spacer { flex: 1; }
    button { font: inherit; color: inherit; cursor: pointer; }
    button:focus-visible { outline: 2px solid #1570ef; outline-offset: 2px; }
    .icon { border: 0; background: transparent; width: 28px; height: 28px; border-radius: 6px; font-size: 16px; }
    .icon:hover { background: #f2f4f7; }
    .content { padding: 12px; }
    .score { display: flex; align-items: baseline; gap: 10px; }
    .pct { font-size: 34px; font-weight: 700; letter-spacing: -.02em; }
    .verdict { font-size: 15px; font-weight: 600; display: inline-flex; align-items: center; gap: 5px; }
    .sg-icon { flex: none; }
    .high .pct, .high .verdict { color: #b42318; }
    .mid .pct, .mid .verdict { color: #b54708; }
    .ok .pct, .ok .verdict { color: #067647; }
    .meter { height: 8px; border-radius: 4px; background: #eaecf0; overflow: hidden; margin: 8px 0 6px; }
    .meter span { display: block; height: 100%; border-radius: 4px; background: #067647; }
    .high .meter span { background: #d92d20; }
    .mid .meter span { background: #f79009; }
    .sub, .models, .foot, .hint { color: #475467; margin: 6px 0; }
    .summary { background: #f9fafb; border-radius: 8px; padding: 8px; margin: 8px 0; }
    .info { list-style: none; margin: 8px 0 0; padding: 8px; border-radius: 8px; background: #f5f8ff;
      display: grid; gap: 4px; color: #344054; font-size: 12px; }
    .ai { margin: 6px 0; font-size: 12px; }
    .labeling { border-top: 1px solid #eaecf0; margin-top: 10px; padding-top: 8px; }
    .label-title, .label-status { margin: 0 0 6px; color: #475467; font-size: 12px; }
    .label-status { margin: 6px 0 0; }
    .label-row { display: flex; gap: 8px; }
    .label-btn { flex: 1; border-radius: 8px; padding: 6px 8px; font-weight: 600; background: transparent;
      border: 1px solid #d0d5dd; }
    .label-btn.scam { color: #b42318; border-color: #fda29b; }
    .label-btn.ok { color: #067647; border-color: #75e0a7; }
    .label-btn:disabled { opacity: .5; cursor: wait; }
    ol { list-style: none; margin: 8px 0 0; padding: 0; display: grid; gap: 4px; }
    .sig { display: grid; grid-template-columns: 16px 1fr; gap: 2px 6px; width: 100%; text-align: left;
      background: transparent; border: 1px solid transparent; border-radius: 8px; padding: 6px; }
    .sig:hover { background: #f9fafb; border-color: #eaecf0; }
    .sig .sev { grid-row: span 3; display: block; width: 10px; height: 10px; margin: 5px 0 0 2px;
      background: currentColor; }
    .sig.high .sev { color: #d92d20; clip-path: polygon(50% 0, 100% 100%, 0 100%); }
    .sig.mid .sev { color: #dc6803; transform: rotate(45deg) scale(.8); border-radius: 1px; }
    .sig.low .sev { color: #ca8504; border-radius: 50%; transform: scale(.8); }
    .sig .ev { color: #475467; font-size: 12px; overflow-wrap: anywhere; }
    .sig .where { color: #667085; font-size: 11px; }
    .link { border: 0; background: none; color: #175cd3; padding: 4px 0; text-decoration: underline; }
    .primary { border: 0; border-radius: 8px; background: #175cd3; color: #fff; padding: 6px 12px; font-weight: 600; }
    .foot { display: flex; align-items: center; justify-content: space-between; gap: 8px;
      border-top: 1px solid #eaecf0; padding-top: 8px; margin-top: 10px; }
    .pill { display: flex; align-items: center; gap: 8px; border: 0; background: transparent; padding: 8px 12px;
      font-weight: 600; width: 100%; }
    .spinner { width: 16px; height: 16px; border: 2px solid #d0d5dd; border-top-color: #175cd3; border-radius: 50%;
      display: inline-block; animation: spin .8s linear infinite; vertical-align: -3px; margin-right: 6px; }
    @keyframes spin { to { transform: rotate(360deg); } }
    .sr-only { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; }
    code { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; background: #f2f4f7; padding: 1px 4px; border-radius: 4px; }
    @media (prefers-reduced-motion: reduce) { .spinner { animation: none; } }
    @media (prefers-color-scheme: dark) {
      .card { color: #f2f4f7; background: #1d2939; border-color: #344054; }
      .head, .foot { border-color: #344054; }
      .icon:hover, .sig:hover { background: #283548; border-color: #344054; }
      .sub, .models, .foot, .hint, .sig .ev, .sig .where { color: #cbd2dc; }
      .summary, code { background: #283548; }
      .info { background: #22314a; color: #cbd2dc; }
      .meter { background: #344054; }
      .high .pct, .high .verdict { color: #fda29b; } .mid .pct, .mid .verdict { color: #fec84b; }
      .ok .pct, .ok .verdict { color: #75e0a7; }
      .link { color: #84caff; }
      .labeling { border-color: #344054; }
      .label-title, .label-status { color: #cbd2dc; }
      .label-btn.scam { color: #fda29b; } .label-btn.ok { color: #75e0a7; }
    }`;

  let host = null;
  let card = null;
  let collapsed = false;
  let lastState = null;
  let showAll = false;
  let labelStatus = null; // bleibt bei der Stufe-2-Aktualisierung erhalten, ein neuer Scan setzt zurück

  function h(tag, props = {}, ...children) {
    const el = document.createElement(tag);
    for (const [key, value] of Object.entries(props)) {
      if (value == null || value === false) continue;
      if (key === "class") el.className = value;
      else if (key === "text") el.textContent = value;
      else if (key.startsWith("on")) el.addEventListener(key.slice(2), value);
      else el.setAttribute(key, value === true ? "" : value);
    }
    for (const child of children.flat()) if (child != null && child !== false) el.append(child);
    return el;
  }

  function ensure() {
    if (host?.isConnected) return;
    host = document.createElement("div");
    host.id = "scamguard-root";
    host.style.cssText = "all: initial; position: fixed; top: 88px; right: 16px; z-index: 2147483647;";
    const shadow = host.attachShadow({ mode: "open" });
    try {
      const sheet = new CSSStyleSheet(); // kein <style>-Tag → unabhängig von der CSP der Seite
      sheet.replaceSync(CSS);
      shadow.adoptedStyleSheets = [sheet];
    } catch {
      // Firefox: Content Scripts dürfen adoptedStyleSheets nicht setzen (Xray-Wrapper) → <style>
      shadow.append(Object.assign(document.createElement("style"), { textContent: CSS }));
    }
    card = h("section", { class: "card", role: "region", "aria-label": "ScamGuard – Betrugsprüfung" });
    shadow.append(card);
    document.documentElement.append(host);
  }

  function header() {
    return h("header", { class: "head" },
      SG.icon("brand", { size: 18 }),
      h("strong", { text: "ScamGuard" }),
      h("span", { class: "spacer" }),
      h("button", {
        class: "icon", type: "button", text: "–", title: "Einklappen", "aria-label": "Panel einklappen",
        onclick: () => { collapsed = true; render(lastState); },
      }),
      h("button", {
        class: "icon", type: "button", text: "×", title: "Panel schließen (Markierungen bleiben)",
        "aria-label": "Panel schließen", onclick: remove,
      }));
  }

  function render(state) {
    lastState = state;
    ensure();
    card.replaceChildren();
    if (collapsed) {
      card.append(renderCollapsed(state));
      return;
    }
    card.append(header());
    const content = h("div", { class: "content" });
    if (state.kind === "loading") content.append(renderLoading(state));
    else if (state.kind === "error") content.append(renderError(state));
    else content.append(...renderResult(state));
    card.append(content);
  }

  function renderCollapsed(state) {
    const text =
      state.kind === "result" ? `${Math.round(state.result.score * 100)} % – ${VERDICTS[state.result.verdict]?.label ?? ""}`
        : state.kind === "loading" ? "prüft …" : "Fehler";
    const cls = state.kind === "result" ? VERDICTS[state.result.verdict]?.cls ?? "" : "";
    return h("button", {
      class: `pill ${cls}`, type: "button", "aria-label": `ScamGuard: ${text}. Panel ausklappen`,
      onclick: () => { collapsed = false; render(lastState); },
    }, SG.icon("brand", { size: 18 }), h("span", { class: "verdict", text: `ScamGuard ${text}` }));
  }

  function renderLoading(state) {
    const what = { selection: "markierten Text", chat: "Chat" }[state.source] ?? "Inserat";
    return h("p", { role: "status", "aria-live": "polite" },
      h("span", { class: "spinner", "aria-hidden": "true" }), `Prüfe ${what} …`);
  }

  function renderError({ error, onRetry }) {
    const offline = error?.kind === "offline" || error?.kind === "timeout";
    return h("div", { role: "alert" },
      h("p", { text: error?.message ?? "Unbekannter Fehler" }),
      offline && h("p", { class: "hint" }, "Server starten mit ", h("code", { text: "scamguard start" })),
      onRetry && h("button", { class: "primary", type: "button", text: "Erneut versuchen", onclick: onRetry }));
  }

  function renderLabeling(onLabel) {
    const status = h("p", { class: "label-status", role: "status", text: labelStatus ?? "" });
    const save = async (label) => {
      buttons.forEach((b) => { b.disabled = true; });
      status.textContent = "Speichere …";
      const r = await onLabel(label);
      labelStatus = r?.ok
        ? `Gespeichert als ${r.label === "betrug" ? "Betrug" : "seriös"} · ${r.count} eigene Labels` +
          (r.bilder == null ? ". " : ` · ${r.bilder} ${r.bilder === 1 ? "Foto" : "Fotos"} abgelegt. `) +
          "Neu trainieren: Web-App → „Meine Daten & Training“."
        : `Speichern fehlgeschlagen: ${r?.error?.message ?? "unbekannter Fehler"}`;
      status.textContent = labelStatus;
      buttons.forEach((b) => { b.disabled = false; });
    };
    const buttons = [
      h("button", { class: "label-btn scam", type: "button", text: "Betrug", onclick: () => save("betrug") }),
      h("button", { class: "label-btn ok", type: "button", text: "Seriös", onclick: () => save("serioes") }),
    ];
    return h("div", { class: "labeling" },
      h("p", { class: "label-title", text: "Deine Einstufung (wird zu Trainingsdaten):" }),
      h("div", { class: "label-row" }, buttons),
      status);
  }

  function renderResult({ result, marked, onFocus, onRescan, onLabel, aiPending, aiError, context }) {
    const pct = Math.round(result.score * 100);
    const v = VERDICTS[result.verdict] ?? { cls: "", icon: null, label: result.verdict };
    const summary = result.signals.find((s) => s.code === "LLM_SUMMARY");
    // Kontext (Anbieterprofil, Händler, Werbung) getrennt von den Warnsignalen anzeigen
    const info = result.signals.filter((s) => s.info);
    const signals = result.signals.filter((s) => s.code !== "LLM_SUMMARY" && !s.info);
    const visible = showAll ? signals : signals.slice(0, MAX_VISIBLE_SIGNALS);
    const markedCount = signals.filter((s) => marked.has(s.id)).length;

    const meterFill = h("span");
    meterFill.style.width = `${pct}%`;

    const parts = [
      h("div", { class: v.cls },
        h("div", { class: "score", role: "status", "aria-live": "polite" },
          h("span", { class: "pct", text: `${pct} %` }),
          h("span", { class: "verdict" }, v.icon && SG.icon(v.icon, { size: 18 }), v.label)),
        h("div", {
          class: "meter", role: "meter", "aria-label": "Betrugswahrscheinlichkeit",
          "aria-valuemin": "0", "aria-valuemax": "100", "aria-valuenow": String(pct),
        }, meterFill)),
      h("p", {
        class: "sub",
        text: signals.length
          ? `${signals.length} Warnsignal${signals.length === 1 ? "" : "e"} · ${markedCount} auf der Seite markiert`
          : "Keine Warnsignale gefunden.",
      }),
    ];
    if (context) parts.push(h("p", { class: "sub", text: context }));
    if (info.length) {
      parts.push(h("ul", { class: "info", "aria-label": "Zum Anbieter" }, info.map((s) => h("li", { text: s.message }))));
    }
    if (summary) parts.push(h("p", { class: "summary" }, h("strong", { text: "KI-Einschätzung: " }), summary.message));
    // KI-Analyse: läuft noch / nicht verfügbar (z. B. lokales Modell nicht gestartet)
    const llm = result.model_results.find((r) => r.name === "llm");
    const llmProblem = aiError ?? (llm?.available && llm.score == null ? llm.error : null);
    if (aiPending) {
      parts.push(h("p", { class: "ai", role: "status" },
        h("span", { class: "spinner", "aria-hidden": "true" }), "KI-Analyse läuft – Ergebnis wird ergänzt …"));
    } else if (llmProblem) {
      parts.push(h("p", { class: "ai hint", role: "status", text: `KI-Analyse nicht verfügbar: ${llmProblem}` }));
    }

    if (visible.length) {
      parts.push(h("ol", { "aria-label": "Warnsignale" }, visible.map((s) => {
        const sev = SG.highlight.severity(s);
        const isMarked = marked.has(s.id);
        return h("li", {}, h("button", {
          class: `sig ${sev}`, type: "button", disabled: !isMarked,
          title: isMarked ? "Zur Fundstelle springen" : "Nicht auf der Seite markierbar",
          onclick: () => onFocus(s.id),
        },
          h("span", { class: "sev", "aria-hidden": "true" }),
          h("span", {}, h("span", { class: "sr-only", text: `${SEVERITY[sev]}: ` }), s.message),
          s.evidence && h("span", { class: "ev", text: `„${s.evidence}“` }),
          h("span", { class: "where", text: isMarked ? "auf der Seite markiert" : "nicht auf der Seite markierbar" })));
      })));
    }
    if (signals.length > MAX_VISIBLE_SIGNALS) {
      parts.push(h("button", {
        class: "link", type: "button",
        text: showAll ? "Weniger anzeigen" : `Alle ${signals.length} anzeigen`,
        onclick: () => { showAll = !showAll; render(lastState); },
      }));
    }

    const models = result.model_results.map((r) => {
      const name = MODEL_NAMES[r.name] ?? r.name;
      const state = !r.available ? "aus" : r.score == null ? "–" : `${Math.round(r.score * 100)} %`;
      return h("span", { title: r.error ?? "" , text: `${name} ${state}` });
    });
    parts.push(h("p", { class: "models" }, "Modelle: ", models.flatMap((m, i) => (i ? [" · ", m] : [m]))));
    if (onLabel) parts.push(renderLabeling(onLabel));
    parts.push(h("div", { class: "foot" },
      h("span", { text: "Einschätzung, kein Beweis." }),
      h("button", { class: "primary", type: "button", text: "Erneut prüfen", onclick: onRescan })));
    return parts;
  }

  function remove() {
    host?.remove();
    host = card = null;
    collapsed = false;
  }

  SG.panel = {
    showLoading: (source) => {
      labelStatus = null;
      render({ kind: "loading", source });
    },
    showError: (error, onRetry) => render({ kind: "error", error, onRetry }),
    /** Inhalt hat sich geändert (neue Chat-Nachricht) → „Gespeichert als …“ gilt nicht mehr. */
    resetLabelStatus: () => { labelStatus = null; },
    showResult: (result, marked, handlers) => {
      showAll = false;
      render({ kind: "result", result, marked, ...handlers });
    },
    remove,
  };
})();
