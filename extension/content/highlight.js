// ScamGuard – Fundstellen auf der Seite markieren (wie ein Adblocker Elemente kennzeichnet).
//
// Text: Fundstellen können über mehrere Textknoten verteilt sein (<br>, <b> …). Deshalb wird ein
// virtueller Gesamttext gebaut (Knoten durch "\n" getrennt), dort gesucht und jeder Treffer auf die
// beteiligten Knoten zurückgerechnet. Elemente (Preis, Verkäufer, Bilder) bekommen einen Rahmen.

(() => {
  const SG = (globalThis.ScamGuard ??= {});
  if (SG.highlight) return;

  const MARK_CLASS = "scamguard-mark";
  const FLAG_CLASS = "scamguard-flagged";
  const PULSE_CLASS = "scamguard-pulse";
  const SEV_CLASSES = ["scamguard-sev-high", "scamguard-sev-mid", "scamguard-sev-low"];
  const SKIP_SELECTOR = "script, style, noscript, textarea, select, svg, [contenteditable], #scamguard-root";
  const MAX_MATCHES_PER_NEEDLE = 10;
  const SEV_RANK = { low: 0, mid: 1, high: 2 };

  let signalsById = new Map();
  const originalTitles = new WeakMap();

  const severity = (s) => (s.hard || s.weight >= 0.6 ? "high" : s.weight >= 0.3 ? "mid" : "low");

  function severityOfIds(ids) {
    let best = "low";
    for (const id of ids) {
      const s = signalsById.get(id);
      if (s && SEV_RANK[severity(s)] > SEV_RANK[best]) best = severity(s);
    }
    return best;
  }

  /** Fundstelle → Regex: Leerraum flexibel, Groß/Klein egal, Wortgrenzen an Buchstaben-Enden. */
  function needleRegex(needle) {
    const trimmed = needle.trim();
    const tokens = trimmed.split(/\s+/).filter(Boolean).map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
    if (!tokens.length) return null;
    const pre = /^[\p{L}\p{N}]/u.test(trimmed) ? "(?<![\\p{L}\\p{N}])" : "";
    const post = /[\p{L}\p{N}]$/u.test(trimmed) ? "(?![\\p{L}\\p{N}])" : "";
    return new RegExp(pre + tokens.join("\\s*") + post, "giu");
  }

  function textNodesUnder(root) {
    const nodes = [];
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
      acceptNode(node) {
        const parent = node.parentElement;
        if (!parent || !node.nodeValue.trim()) return NodeFilter.FILTER_SKIP;
        if (parent.closest(SKIP_SELECTOR) || parent.closest(`mark.${MARK_CLASS}`)) return NodeFilter.FILTER_SKIP;
        return NodeFilter.FILTER_ACCEPT;
      },
    });
    for (let n = walker.nextNode(); n; n = walker.nextNode()) nodes.push(n);
    return nodes;
  }

  function addSignals(el, signals) {
    const ids = new Set((el.dataset.scamguardIds ?? "").split(" ").filter(Boolean));
    for (const s of signals) ids.add(String(s.id));
    el.dataset.scamguardIds = [...ids].join(" ");
    el.classList.remove(...SEV_CLASSES);
    el.classList.add(`scamguard-sev-${severityOfIds([...ids].map(Number))}`);
    const lines = [...ids].map((id) => signalsById.get(Number(id))?.message).filter(Boolean);
    el.title = `ScamGuard-Warnsignal:\n• ${lines.join("\n• ")}`;
  }

  function wrapRange(node, start, end, signals) {
    const middle = node.splitText(start);
    middle.splitText(end - start);
    const mark = document.createElement("mark");
    mark.className = MARK_CLASS;
    middle.replaceWith(mark);
    mark.appendChild(middle);
    addSignals(mark, signals);
    return mark;
  }

  /** Markiert alle Vorkommen von `needle` in `roots`. Rückgabe: Anzahl Treffer. */
  function markText(roots, needle, signals) {
    const re = needleRegex(needle);
    if (!re) return 0;
    let count = 0;
    for (const root of roots) {
      const nodes = textNodesUnder(root);
      const starts = [];
      let full = "";
      for (const n of nodes) {
        starts.push(full.length);
        full += `${n.nodeValue}\n`;
      }
      const ranges = [];
      for (const m of full.matchAll(re)) {
        if (m[0].length) ranges.push([m.index, m.index + m[0].length]);
        if (ranges.length >= MAX_MATCHES_PER_NEEDLE) break;
      }
      // Rückwärts: Spätere Treffer zuerst umschließen, damit frühere Offsets gültig bleiben
      for (const [s, e] of ranges.reverse()) {
        for (let i = nodes.length - 1; i >= 0; i--) {
          const ns = starts[i];
          const ne = ns + nodes[i].nodeValue.length;
          if (ne <= s || ns >= e) continue;
          wrapRange(nodes[i], Math.max(s, ns) - ns, Math.min(e, ne) - ns, signals);
        }
        count++;
      }
    }
    // Steckt die Fundstelle in einer schon markierten längeren Stelle? Dann dort mit eintragen.
    if (count === 0) {
      for (const mark of document.querySelectorAll(`mark.${MARK_CLASS}`)) {
        re.lastIndex = 0;
        if (re.test(mark.textContent)) {
          addSignals(mark, signals);
          count++;
        }
      }
    }
    return count;
  }

  function markElement(el, signals) {
    if (!el?.isConnected) return false;
    if (!el.classList.contains(FLAG_CLASS)) {
      originalTitles.set(el, el.getAttribute("title"));
      el.classList.add(FLAG_CLASS);
    }
    addSignals(el, signals);
    return true;
  }

  /** Container mit Markierungen (z. B. Chat-Nachrichten) selbst einrahmen: Welche Nachricht ist auffällig?
   *  Rückgabe: Anzahl eingerahmter Container. */
  function flagContainers(elements) {
    let count = 0;
    for (const el of elements ?? []) {
      const ids = new Set();
      for (const mark of el.querySelectorAll(`mark.${MARK_CLASS}`)) {
        for (const id of (mark.dataset.scamguardIds ?? "").split(" ").filter(Boolean)) ids.add(Number(id));
      }
      const signals = [...ids].map((id) => signalsById.get(id)).filter(Boolean);
      if (signals.length && markElement(el, signals)) count++;
    }
    return count;
  }

  function clearAll() {
    const parents = new Set();
    for (const mark of document.querySelectorAll(`mark.${MARK_CLASS}`)) {
      parents.add(mark.parentNode);
      mark.replaceWith(...mark.childNodes);
    }
    for (const parent of parents) parent?.normalize();
    for (const el of document.querySelectorAll(`.${FLAG_CLASS}`)) {
      el.classList.remove(FLAG_CLASS, PULSE_CLASS, ...SEV_CLASSES);
      const title = originalTitles.get(el);
      if (title == null) el.removeAttribute("title");
      else el.setAttribute("title", title);
      delete el.dataset.scamguardIds;
    }
    signalsById = new Map();
  }

  /** Neuer Durchlauf: alte Markierungen weg, Signale registrieren (id = Index). */
  function begin(signals) {
    clearAll();
    signals.forEach((s, i) => {
      s.id = i;
      signalsById.set(i, s);
    });
  }

  function focus(id) {
    const el = document.querySelector(`[data-scamguard-ids~="${CSS.escape(String(id))}"]`);
    if (!el) return false;
    const reduceMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;
    el.scrollIntoView({ behavior: reduceMotion ? "auto" : "smooth", block: "center" });
    el.classList.remove(PULSE_CLASS);
    void el.offsetWidth; // Animation neu starten
    el.classList.add(PULSE_CLASS);
    setTimeout(() => el.classList.remove(PULSE_CLASS), 2600);
    return true;
  }

  SG.highlight = { begin, markText, markElement, flagContainers, clearAll, focus, severity };
})();
