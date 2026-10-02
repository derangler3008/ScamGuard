// ScamGuard – schlichte Linien-Icons als SVG (statt Emojis, die je nach System anders aussehen).
// Klassisches Skript: Content Scripts (manifest.json) und Popup (popup.html) laden es vor ihrem Code.

(() => {
  const SG = (globalThis.ScamGuard ??= {});
  if (SG.icon) return;

  const NS = "http://www.w3.org/2000/svg";
  // 24×24, Strichstärke 2, runde Enden – gleiche Formensprache wie das Toolbar-Icon
  const PATHS = {
    danger: ["M8 2.5h8l5.5 5.5v8L16 21.5H8L2.5 16V8z", "M9 9l6 6", "M15 9l-6 6"],
    warning: ["M10.3 4.2 2.6 17.5A2 2 0 0 0 4.3 20.5h15.4a2 2 0 0 0 1.7-3L13.7 4.2a2 2 0 0 0-3.4 0z",
      "M12 9.5v4", "M12 17h.01"],
    ok: ["M12 2.5a9.5 9.5 0 1 0 0 19 9.5 9.5 0 0 0 0-19z", "M8 12.4l2.8 2.8L16.2 9.8"],
  };

  function svg(size, label) {
    const el = document.createElementNS(NS, "svg");
    for (const [k, v] of Object.entries({ viewBox: "0 0 24 24", width: size, height: size, class: "sg-icon" })) {
      el.setAttribute(k, String(v));
    }
    if (label) {
      el.setAttribute("role", "img");
      el.setAttribute("aria-label", label);
    } else {
      el.setAttribute("aria-hidden", "true");
    }
    return el;
  }

  function path(d, attrs) {
    const el = document.createElementNS(NS, "path");
    el.setAttribute("d", d);
    for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
    return el;
  }

  /** Icon als SVG-Element. name: "brand" | "danger" | "warning" | "ok". Farbe über CSS `color`. */
  SG.icon = (name, { size = 16, label } = {}) => {
    const el = svg(size, label);
    if (name === "brand") {
      // Schild mit Lupe wie das Toolbar-Icon (icons/icon*.png)
      el.append(
        path("M12 1.8 3.5 5v6.4c0 5 3.6 9.3 8.5 10.8 4.9-1.5 8.5-5.8 8.5-10.8V5z", { fill: "#2457d6" }),
        path("M11 7.2a3.8 3.8 0 1 0 0 7.6 3.8 3.8 0 0 0 0-7.6z", {
          fill: "none", stroke: "#fff", "stroke-width": "2" }),
        path("M13.8 13.8l2.7 2.7", { fill: "none", stroke: "#fff", "stroke-width": "2.2", "stroke-linecap": "round" }));
      return el;
    }
    for (const d of PATHS[name] ?? []) {
      el.append(path(d, { fill: "none", stroke: "currentColor", "stroke-width": "2",
        "stroke-linecap": "round", "stroke-linejoin": "round" }));
    }
    return el;
  };
})();
