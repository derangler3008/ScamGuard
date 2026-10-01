// ScamGuard – Inserat aus der Seite auslesen.
// Adapter pro Plattform; neue Seiten (z. B. willhaben.at, markt.de) als weiteres Objekt in ADAPTERS ergänzen.

(() => {
  const SG = (globalThis.ScamGuard ??= {});
  if (SG.extract) return;

  const MAX_GENERIC_TEXT = 15_000;

  const ownText = (el) =>
    [...el.childNodes]
      .filter((n) => n.nodeType === Node.TEXT_NODE)
      .map((n) => n.textContent)
      .join(" ")
      .replace(/\s+/g, " ")
      .trim();

  function daysSince(text) {
    const m = /(\d{1,2})\.(\d{1,2})\.(\d{4})/.exec(text ?? "");
    if (!m) return null;
    const date = new Date(Number(m[3]), Number(m[2]) - 1, Number(m[1]));
    return Math.max(0, Math.floor((Date.now() - date.getTime()) / 86_400_000));
  }

  // ------------------------------------------------------------------------- kleinanzeigen.de

  const kleinanzeigen = {
    name: "kleinanzeigen",
    matches: () =>
      /(^|\.)kleinanzeigen\.de$/.test(location.hostname) && location.pathname.startsWith("/s-anzeige/"),

    extract() {
      const $ = (sel) => document.querySelector(sel);
      const titleEl = $("#viewad-title");
      const descEl = $("#viewad-description-text");
      if (!titleEl && !descEl) return null; // Layout geändert → generischer Modus
      const priceEl = $("#viewad-price");
      const sellerEl =
        [...document.querySelectorAll("#viewad-contact .userprofile-vip-details-text")].find((e) =>
          /aktiv seit/i.test(e.textContent)) ?? $("#viewad-contact");
      const crumbs = [...document.querySelectorAll("#vap-brdcrmb .breadcrump-link")]
        .map((a) => a.textContent.trim())
        .filter(Boolean);

      // Galerie: gleiche URL nur einmal; ".AUTO" liefert evtl. AVIF → JPEG anfordern
      const seen = new Set();
      const images = [];
      for (const img of document.querySelectorAll("#viewad-product .galleryimage-element img")) {
        const raw = img.getAttribute("data-imgsrc") || img.currentSrc || img.src || "";
        const url = raw.replace(/(rule=\$_\d+)\.AUTO\b/, "$1.JPG");
        if (!url.startsWith("https://") || seen.has(url)) continue;
        seen.add(url);
        images.push({ el: img, url });
      }

      return {
        source: "kleinanzeigen",
        listing: {
          title: titleEl ? ownText(titleEl) : "",
          description: descEl ? descEl.innerText.trim() : "",
          price: priceEl ? priceEl.textContent.trim() : null, // Rohtext, Server normalisiert
          category: crumbs.join(" > "),
          seller_account_age_days: sellerEl ? daysSince(sellerEl.textContent) : null,
          messages: [],
        },
        imageUrls: images.map((i) => i.url),
        elements: { price: priceEl, seller: sellerEl, images: images.map((i) => i.el) },
        roots: [$("#viewad-main") ?? document.body],
      };
    },
  };

  const ADAPTERS = [kleinanzeigen];

  // ------------------------------------------------------------------------- generisch & Auswahl

  function generic() {
    const meta = (key) =>
      document.querySelector(`meta[property="${key}"], meta[name="${key}"]`)?.getAttribute("content")?.trim() ?? "";
    const main = document.querySelector("main, [role='main'], article") ?? document.body;
    const price =
      meta("product:price:amount") ||
      meta("og:price:amount") ||
      document.querySelector("[itemprop='price']")?.getAttribute("content") ||
      null;
    return {
      source: "generic",
      listing: {
        title: meta("og:title") || document.querySelector("h1")?.innerText.trim() || document.title,
        description: (main.innerText ?? "").slice(0, MAX_GENERIC_TEXT),
        price,
        category: "",
        messages: [],
      },
      imageUrls: [],
      elements: { images: [] },
      roots: [main],
    };
  }

  function selection(text) {
    return {
      source: "selection",
      listing: { title: "", description: "", price: null, category: "", messages: [text] },
      imageUrls: [],
      elements: { images: [] },
      roots: [document.body],
    };
  }

  SG.extract = {
    /** Gibt es für diese Seite einen Plattform-Adapter (→ automatischer Scan)? */
    isListingPage: () => ADAPTERS.some((a) => a.matches()),

    /** mode: "auto" | "page" → Plattform-Adapter, sonst generisch; "selection" → markierter Text. */
    forMode(mode, text) {
      if (mode === "selection") return selection((text ?? "").trim() || String(getSelection() ?? ""));
      for (const adapter of ADAPTERS) {
        if (!adapter.matches()) continue;
        const extracted = adapter.extract();
        if (extracted) return extracted;
      }
      return generic();
    },
  };
})();
