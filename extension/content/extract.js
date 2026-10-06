// ScamGuard – Inserat bzw. Chatverlauf aus der Seite auslesen.
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

  // Inseratsfotos liegen auf dem Bild-Server unter /prod-ads/images/ (Avatare, Banner nicht)
  const AD_IMAGE = /^https:\/\/img\.kleinanzeigen\.de\/api\/v1\/prod-ads\/images\//;

  // Rechtsform am Ende eines Anbieter-/Firmennamens (gleiche Liste wie schema.LEGAL_FORM im Server)
  const LEGAL_FORM =
    /(?<![\w.])(GmbH\s*&\s*Co\.?\s*KG|gGmbH|GmbH|UG(?:\s*\(haftungsbeschränkt\))?|AG|SE|KGaA|KG|OHG|GbR|PartG(?:mbB)?|eG|e\.\s?K\.|e\.\s?Kfm\.|e\.\s?V\.|Ltd\.?|Inc\.?|LLC|S\.?à\s?r\.?l\.?)\s*$/;

  function commonAncestor(a, b) {
    let node = a;
    while (node && !node.contains(b)) node = node.parentElement;
    return node;
  }

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
      // Seit 10/2026 gibt es zwei Layouts: das alte mit sprechenden Klassen und ein neues mit
      // Utility-Klassen (Tailwind). Stabil in beiden: IDs und die schema.org-Auszeichnung.
      const sellerEl =
        [...document.querySelectorAll("#viewad-contact *")].find(
          (e) => !e.children.length && /aktiv seit/i.test(e.textContent)) ?? $("#viewad-contact");
      const crumbEls = document.querySelectorAll("#vap-brdcrmb [itemprop='itemListElement'] [itemprop='name']");
      const crumbs = [...(crumbEls.length ? crumbEls : document.querySelectorAll("#vap-brdcrmb .breadcrump-link"))]
        .map((e) => e.textContent.trim())
        .filter(Boolean);
      // Anbieterbox: privat/gewerblich, Bewertungs-Abzeichen, Zahl der Anzeigen – keine Namen
      const profile = $("#viewad-profile-box") ?? $("#viewad-contact");
      const sellerTexts = profile
        ? [...profile.querySelectorAll("*")]
            .filter((e) => !e.children.length)
            .map((e) => e.textContent.replace(/\s+/g, " ").trim())
            .filter(Boolean)
        : [];
      const commercial =
        sellerTexts.some((t) => /^gewerblicher (nutzer|anbieter)$/i.test(t)) ||
        Boolean($("#viewad-imprint-section") || $("#viewad-bizteaser"));
      const sellerType = commercial
        ? "gewerblich"
        : sellerTexts.some((t) => /^privater (nutzer|anbieter)$/i.test(t)) ? "privat" : null;
      const badges = sellerTexts.filter((t) =>
        /zufriedenheit$|^(sehr )?freundlich$|^(besonders )?zuverlässig$/i.test(t));
      // „7 Anzeigen online“ oder „248“ + „Anzeigen“ als getrennte Elemente
      const adIndex = sellerTexts.findIndex((t, i) =>
        /^\d+ Anzeigen\b/i.test(t) || (/^\d+$/.test(t) && /^Anzeigen\b/i.test(sellerTexts[i + 1] ?? "")));
      const adCount = adIndex >= 0 ? Number(sellerTexts[adIndex].match(/^\d+/)[0]) : null;
      // Name nur für die Rechtsform (GmbH, UG, GbR …) – übertragen wird die Rechtsform, nie der Name
      const teaser = [...($("#viewad-bizteaser")?.querySelectorAll("*") ?? [])]
        .filter((e) => !e.children.length)
        .map((e) => e.textContent.trim());
      const legalForm =
        [...sellerTexts, ...teaser].map((t) => LEGAL_FORM.exec(t.slice(0, 80))?.[1]).find(Boolean) ?? null;
      // Bereich mit Titel, Galerie und Beschreibung – ohne Seitenleiste und „ähnliche Anzeigen“
      const root = $("#viewad-main") ?? commonAncestor(titleEl ?? descEl, descEl ?? titleEl) ?? document.body;

      // Galerie: gleiche URL nur einmal; ".AUTO" liefert evtl. AVIF → JPEG anfordern
      const gallery = document.querySelectorAll("#viewad-product .galleryimage-element img");
      const candidates = gallery.length ? gallery : root.querySelectorAll("img");
      const seen = new Set();
      const images = [];
      for (const img of candidates) {
        const raw = img.getAttribute("data-imgsrc") || img.currentSrc || img.src || "";
        if (!gallery.length && !AD_IMAGE.test(raw)) continue; // neues Layout: nur Inseratsfotos
        const url = raw.replace(/(rule=\$_\d+)\.AUTO\b/, "$1.JPG");
        const key = url.split("?")[0];
        if (!url.startsWith("https://") || seen.has(key)) continue;
        seen.add(key);
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
          seller_type: sellerType,
          seller_badges: badges,
          seller_num_ads: adCount,
          seller_legal_form: legalForm,
          messages: [],
        },
        imageUrls: images.map((i) => i.url),
        elements: { price: priceEl, seller: sellerEl, images: images.map((i) => i.el) },
        roots: [root],
      };
    },
  };

  // ------------------------------------------------------------------------- Kleinanzeigen-Postfach

  // Das Postfach (m-nachrichten.html) ist eine App ohne stabile IDs und nur eingeloggt sichtbar.
  // Darum keine festen Selektoren: Verlauf = ARIA-Log bzw. Scrollbereich (chatRoot), Nachrichten =
  // die innersten Textblöcke darin. Neue Nachrichten erkennt content.js per MutationObserver.
  const CHAT_SKIP = "script, style, noscript, textarea, input, select, button, svg, nav, header, footer, " +
    "form, [contenteditable], [aria-hidden='true'], #scamguard-root";
  const CHAT_MAX_MESSAGES = 60; // die neuesten – ältere ändern das Urteil selten, kosten aber Zeit
  const CHAT_MAX_CHARS = 4000;
  // Reine Zeit-/Statusangaben sind keine Nachrichten: „14:32“, „Heute, 14:32“, „03.10.2026“, „Gelesen“
  const CHAT_NOISE = /^((heute|gestern|vorgestern)[\s,]*)?(\d{1,2}[.:]\d{2}([.:]\d{2,4})?)?(\s*uhr)?$|^(gelesen|zugestellt|gesendet)$/i;

  const isBlock = (el) => {
    const display = getComputedStyle(el).display;
    return !display.startsWith("inline") && display !== "contents";
  };

  /** Nachrichtenverlauf: ARIA-Log, sonst der größte *innerste* Scrollbereich (Verlauf und Liste der
   *  Unterhaltungen scrollen getrennt, der Verlauf ist breiter). Zählt auch, wenn ein kurzer Chat noch
   *  gar nicht scrollt – entscheidend ist overflow: auto/scroll. Notfalls <main>. */
  function chatRoot() {
    const log = document.querySelector("[role='log']");
    if (log?.innerText.trim()) return log;
    const scrollers = [...document.body.querySelectorAll("*")].filter((el) =>
      el.clientHeight > 80 && !el.closest(CHAT_SKIP) && /(auto|scroll)/.test(getComputedStyle(el).overflowY)
      && el.innerText.trim());
    const innermost = scrollers.filter((el) => !scrollers.some((other) => other !== el && el.contains(other)));
    const best = innermost.sort((x, y) => y.clientWidth * y.clientHeight - x.clientWidth * x.clientHeight)[0];
    return best ?? document.querySelector("main, [role='main']") ?? document.body;
  }

  function chatMessages(root) {
    const blocks = new Set();
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
      acceptNode: (n) => (n.nodeValue.trim() && !n.parentElement?.closest(CHAT_SKIP)
        ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_SKIP),
    });
    for (let n = walker.nextNode(); n; n = walker.nextNode()) {
      let el = n.parentElement;
      while (el && el !== root && !isBlock(el)) el = el.parentElement;
      if (el) blocks.add(el);
    }
    // Nur innerste Blöcke: Wer einen anderen Textblock enthält, ist ein Container (Verlauf, Sprechblase)
    const containers = new Set();
    for (const el of blocks) {
      for (let p = el.parentElement; p && root.contains(p); p = p.parentElement) {
        if (blocks.has(p)) containers.add(p);
      }
    }
    const messages = [];
    for (const el of blocks) {
      if (containers.has(el)) continue;
      const text = el.innerText.trim();
      if (text.length < 2 || text.length > CHAT_MAX_CHARS || CHAT_NOISE.test(text)) continue;
      if (messages.at(-1)?.text === text) continue;
      messages.push({ el, text });
    }
    return messages.slice(-CHAT_MAX_MESSAGES);
  }

  const kleinanzeigenChat = {
    name: "kleinanzeigen-chat",
    matches: () =>
      /(^|\.)kleinanzeigen\.de$/.test(location.hostname) && location.pathname.startsWith("/m-nachrichten"),

    extract() {
      const root = chatRoot();
      const messages = chatMessages(root);
      if (!messages.length) return null; // Verlauf noch nicht geladen
      return {
        source: "chat",
        listing: { title: "", description: "", price: null, category: "", messages: messages.map((m) => m.text) },
        imageUrls: [],
        elements: { images: [], messages: messages.map((m) => m.el) },
        roots: [root],
      };
    },
  };

  const ADAPTERS = [kleinanzeigen, kleinanzeigenChat];

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

    /** Kleinanzeigen-Postfach: Nachrichten kommen ohne Neuladen dazu → beobachten. */
    isChatPage: () => kleinanzeigenChat.matches(),

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
