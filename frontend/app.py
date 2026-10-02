"""ScamGuard – Streamlit-Frontend.

Start:  scamguard ui      (oder: streamlit run frontend/app.py)
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import streamlit as st

# Erlaubt `streamlit run frontend/app.py` auch ohne `pip install -e .`
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from scamguard.config import load_config, resolve_path
from scamguard.data.discovery import IMAGE_DIR, LISTING_DIR, TEXT_DIR
from scamguard.data.labels import LABEL_FILE, count_labels, save_label, save_label_images
from scamguard.data.listing_import import SUPPORTED_SUFFIXES, ImportResult, import_listing
from scamguard.data.loaders import IMAGE_SUFFIXES
from scamguard.data.registry import get_specs
from scamguard.pipeline import ScamGuard
from scamguard.schema import CATEGORIES, Listing, ScanResult
from scamguard.training import DEMO_DATASET, retrain

SAMPLE_FILE = "data/samples/sample_listings.jsonl"
SCAM_TYPES = {
    "unbekannt": "weiß nicht / egal",
    "fake_zahlungslink": "Fake-Zahlungslink („Sicher bezahlen“, DHL …)",
    "vorkasse": "Vorkasse – Ware kommt nie",
    "paypal_freunde": "PayPal „Freunde & Familie“",
    "dreiecksbetrug": "Dreiecksbetrug",
    "phishing": "Phishing (Daten/Login abgreifen)",
    "identitaetsdiebstahl": "Ausweis/Identität abgreifen",
    "ueberzahlung": "Überzahlung mit Rückforderung",
    "fake_inserat_sonstiges": "sonstiges Fake-Inserat",
}
CATEGORY_LABELS = {
    "elektronik": "Elektronik", "haushaltsgeraete": "Haushaltsgeräte", "auto": "Auto & Fahrzeuge",
    "moebel": "Möbel", "mode": "Mode", "tiere": "Tiere", "immobilien": "Immobilien",
    "tickets": "Tickets", "sonstiges": "Sonstiges",
}
UPLOAD_TYPES = sorted(s.lstrip(".") for s in SUPPORTED_SUFFIXES)
CHAT_TYPES = sorted(s.lstrip(".") for s in IMAGE_SUFFIXES | {".txt"})

st.set_page_config(page_title="ScamGuard – Kleinanzeigen-Check", page_icon="🛡️", layout="wide")

# Startwerte des Formulars „Felder selbst eingeben“ (Widgets lesen sie über ihren key)
for _key, _default in {"title": "", "description": "", "messages": "", "price": None,
                       "category": "sonstiges", "age": None, "ratings": None}.items():
    st.session_state.setdefault(_key, _default)


@st.cache_resource(show_spinner="Modelle werden geladen …")
def get_guard(use_llm: bool, send_images: bool) -> ScamGuard:
    return ScamGuard(overrides={"llm": {"enabled": use_llm, "send_images": send_images}})


@st.cache_data(show_spinner="Lese das Inserat …", max_entries=32)
def read_upload(files: tuple[tuple[str, bytes], ...], text: str,
                chat_files: tuple[tuple[str, bytes], ...], chat_text: str) -> ImportResult:
    return import_listing(list(files), text, list(chat_files), chat_text)


def load_example(want_scam: bool) -> None:
    path = resolve_path(SAMPLE_FILE)
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    row = next(r for r in rows if bool(r.get("label")) == want_scam)
    st.session_state.update({
        "title": row.get("title", ""),
        "description": row.get("description", ""),
        "price": row.get("price"),
        "category": row.get("category", "sonstiges"),
        "messages": "\n\n---\n\n".join(row.get("messages", [])),
        "age": row.get("seller_account_age_days"),
        "ratings": row.get("seller_num_ratings"),
    })


def split_messages(raw: str) -> list[str]:
    return [m.strip() for m in (raw or "").split("---") if m.strip()]


def build_listing() -> Listing:
    return Listing(
        title=st.session_state.get("title") or "",
        description=st.session_state.get("description") or "",
        price=st.session_state.get("price"),
        category=st.session_state.get("category") or "sonstiges",
        seller_account_age_days=st.session_state.get("age"),
        seller_num_ratings=st.session_state.get("ratings"),
        messages=split_messages(st.session_state.get("messages")),
    )


def scan(listing: Listing, images: list[tuple[str, bytes]]) -> ScanResult:
    """Bilder nur für die Dauer des Scans auf die Platte schreiben."""
    with tempfile.TemporaryDirectory(prefix="scamguard_ui_") as tmp:
        paths = []
        for i, (name, data) in enumerate(images):
            path = Path(tmp) / f"{i}{Path(name).suffix.lower() or '.jpg'}"
            path.write_bytes(data)
            paths.append(str(path))
        return guard.scan(Listing.from_dict({**listing.to_dict(), "image_paths": paths}))


def cached_scan(listing: Listing, images: list[tuple[str, bytes]]) -> ScanResult:
    """Streamlit führt das Skript bei jedem Klick neu aus – Ergebnis merken (KI-Analyse dauert)."""
    key = hashlib.sha1(json.dumps(
        [listing.to_dict(), [hashlib.sha1(d).hexdigest() for _, d in images], use_llm],
        sort_keys=True, default=str).encode()).hexdigest()
    cache: dict[str, ScanResult] = st.session_state.setdefault("scan_cache", {})
    if key not in cache:
        with st.spinner("ScamGuard prüft …"):
            cache[key] = scan(listing, images)
        while len(cache) > 8:
            cache.pop(next(iter(cache)))
    return cache[key]


def save_labeled(listing: Listing, images: list[tuple[str, bytes]], scam: bool, scam_type: str) -> int:
    """Gleicher Speicherweg wie die Extension; ein erneut eingestuftes Inserat ersetzt das alte."""
    stored = Listing.from_dict({
        **listing.to_dict(),
        "image_paths": save_label_images([(Path(n).suffix.lower() or ".jpg", d) for n, d in images]),
        "label": int(scam),
        "scam_type": scam_type if scam and scam_type != "unbekannt" else None,
        "source": "eigene_labels",
    })
    return save_label(stored)


def counted(total: int) -> str:
    return f"{total} Inserat{'' if total == 1 else 'e'} eingestuft"


def label_buttons(key: str) -> bool | None:
    """True = Betrug, False = seriös, None = (noch) nicht geklickt."""
    st.selectbox("Masche (optional, nur bei Betrug)", list(SCAM_TYPES), key=f"{key}_type",
                 format_func=SCAM_TYPES.get)
    left, right, _ = st.columns([1, 1, 2])
    if left.button("⚠ Betrug", key=f"{key}_scam", type="primary", width="stretch"):
        return True
    if right.button("✓ Seriös", key=f"{key}_ok", width="stretch"):
        return False
    return None


def render_result(result: ScanResult, compact: bool = False) -> None:
    pct = round(result.score * 100)
    if result.verdict == "hohes Risiko":
        st.error(f"**Hohes Risiko – {pct} %**  \nFinger weg: mehrere starke Betrugsmerkmale.", icon="⛔")
    elif result.verdict == "verdächtig":
        st.warning(f"**Verdächtig – {pct} %**  \nVorsicht, prüfe die Warnsignale unten genau.", icon="⚠️")
    else:
        st.success(f"**Unauffällig – {pct} %**  \nKeine deutlichen Betrugsmerkmale gefunden.", icon="✅")
    if not compact:
        st.progress(result.score, text=f"Betrugswahrscheinlichkeit (Modell-Schätzung): {pct} %")

    shown = result.signals[:3] if compact else result.signals
    if not compact:
        st.subheader("Warnsignale")
    if not result.signals:
        st.write("Keine Warnsignale gefunden.")
    for s in shown:
        marker = "🔴" if s.hard or s.weight >= 0.6 else "🟠" if s.weight >= 0.3 else "🟡"
        evidence = f"  \n<small>Fundstelle: `{s.evidence}`</small>" if s.evidence else ""
        st.markdown(f"{marker} **{s.message}** · _{s.source}_{evidence}", unsafe_allow_html=True)
    if compact and len(result.signals) > 3:
        st.caption(f"… und {len(result.signals) - 3} weitere Warnsignale")

    with st.expander("Einzelmodelle"):
        st.dataframe([
            {"Modell": r.name,
             "Score": None if r.score is None else round(r.score, 3),
             "Status": "aktiv" if r.available and r.score is not None else "inaktiv",
             "Hinweis": r.error or ""}
            for r in result.model_results
        ], hide_index=True, width="stretch")
    if compact:
        return
    with st.expander("So schützt du dich"):
        st.markdown(
            "- Bezahle nur über die Bezahlfunktion der Plattform oder bar bei Abholung.\n"
            "- Klicke keine Zahlungs- oder „Geld empfangen“-Links aus Chats oder E-Mails.\n"
            "- Schicke niemals Ausweis, Selfies oder Bestätigungscodes.\n"
            "- Kein PayPal „Freunde & Familie“ an Fremde, keine Gutscheinkarten.\n"
            "- Im Zweifel: Bilder per Rückwärtssuche prüfen und Inserat der Plattform melden."
        )


def review_fields(result: ImportResult, key: str) -> Listing:
    """Zeigt, was erkannt wurde – korrigieren ist möglich, aber nicht nötig."""
    found = result.listing
    summary = st.container(border=True)
    with st.expander("Erkannte Felder ansehen oder korrigieren",
                     expanded=not (found.title or found.description)):
        c1, c2 = st.columns([2, 1])
        title = c1.text_input("Titel", found.title, key=f"{key}_title")
        price = c2.number_input("Preis in €", value=found.price, min_value=0.0, step=1.0, key=f"{key}_price")
        description = st.text_area("Beschreibung", found.description, height=200, key=f"{key}_desc")
        c3, c4 = st.columns(2)
        category = c3.selectbox("Kategorie", CATEGORIES, index=CATEGORIES.index(found.category),
                                format_func=lambda c: CATEGORY_LABELS.get(c, c), key=f"{key}_cat")
        age = c4.number_input("Kontoalter des Anbieters (Tage)", value=found.seller_account_age_days,
                              min_value=0, step=1, key=f"{key}_age")
        messages = st.text_area("Chat (mehrere Nachrichten mit „---“ trennen)",
                                "\n\n---\n\n".join(found.messages), height=100, key=f"{key}_chat")
    listing = Listing.from_dict({**found.to_dict(), "title": title, "description": description,
                                 "price": price, "category": category, "seller_account_age_days": age,
                                 "messages": split_messages(messages)})

    facts = [f"{listing.price:,.0f} €".replace(",", ".") if listing.price is not None else "Preis ?",
             CATEGORY_LABELS.get(listing.category, listing.category)]
    if listing.location:
        facts.append(listing.location)
    if listing.seller_account_age_days is not None:
        facts.append(f"Konto {listing.seller_account_age_days} Tage alt")
    if listing.messages:
        facts.append(f"{len(listing.messages)} Chat-Nachricht(en)")
    summary.markdown(f"**{listing.title or '(kein Titel erkannt)'}**  \n{' · '.join(facts)}")
    if listing.description:
        preview = listing.description[:280] + ("…" if len(listing.description) > 280 else "")
        summary.caption(preview)
    if result.photos:
        summary.image([d for _, d in result.photos], width=110)
    return listing


# --------------------------------------------------------------------------- Seitenleiste

with st.sidebar:
    st.header("Einstellungen")
    llm_cfg = load_config()["llm"]
    is_local = llm_cfg.get("provider", "local") == "local"
    llm_model = llm_cfg.get(llm_cfg.get("provider", "local"), {}).get("model", "").split("/")[-1]
    use_llm = st.toggle(f"KI-Analyse zuschalten ({llm_model})", value=False,
                        help=("Lokales Modell: kostenlos, Daten bleiben auf dem Rechner. "
                              "Server vorher starten: `scamguard start` oder `scamguard llm-server`. Dauer auf einem MacBook M4: ca. 5–20 s pro Prüfung."
                              if is_local else
                              "Sendet Titel, Beschreibung und Nachrichten an die Claude API. "
                              "Verkäufername und Ort werden nicht übertragen. Kostet API-Guthaben."))
    send_images = st.toggle("Bilder an LLM senden", value=False, disabled=not use_llm or is_local,
                            help="Nur mit Claude (provider: anthropic) möglich.")
    guard = get_guard(use_llm, send_images)

    st.header("Modellstatus")
    for det in guard.detectors:
        if det.available:
            st.markdown(f"✅ **{det.name}**")
        else:
            st.markdown(f"⚪ **{det.name}**  \n<small>{det.unavailable_reason}</small>", unsafe_allow_html=True)

st.title("🛡️ ScamGuard")
st.caption("Betrugserkennung für deutschsprachige Kleinanzeigen · DHBW Mannheim KI-Projekt · "
           "Ergebnis ist eine Einschätzung, kein Beweis.")

tab_upload, tab_manual, tab_data, tab_about = st.tabs(
    ["📥 Inserat hochladen & einstufen", "✏️ Felder selbst eingeben", "📊 Meine Daten & Training",
     "ℹ️ Über das Projekt"])

# --------------------------------------------------------------------------- Hochladen & einstufen

with tab_upload:
    if flash := st.session_state.pop("flash", None):
        st.success(flash, icon="✅")
    st.markdown("**So geht's:** ① Inserat hineinziehen → ② kurz ansehen, was erkannt wurde → "
                "③ **Betrug** oder **Seriös** klicken. Jede Einstufung wird zu Trainingsdaten.")
    n = st.session_state.setdefault("upload_round", 0)  # neue Runde = leere Upload-Felder
    files = st.file_uploader(
        "Inserat hochladen", type=UPLOAD_TYPES, accept_multiple_files=True, key=f"files_{n}",
        help="Screenshots (gern mehrere einer Anzeige), gespeicherte Seite (.html über „Seite speichern "
             "unter …“), PDF („Drucken → Als PDF speichern“) oder .txt. Fotos ohne Text werden als "
             "Produktfotos übernommen.")
    pasted = st.text_area("… oder Text der Anzeige einfügen", key=f"paste_{n}", height=100,
                          placeholder="Auf der Anzeige ⌘A und ⌘C (Windows: Strg+A, Strg+C), hier einfügen")
    with st.expander("Chatverlauf mit dem Anbieter (optional – dort stecken oft die Betrugs-Links)"):
        chat_files = st.file_uploader("Chat-Screenshots", type=CHAT_TYPES, accept_multiple_files=True,
                                      key=f"chatfiles_{n}")
        chat_text = st.text_area("… oder Chat-Text einfügen", key=f"chat_{n}", height=90)

    file_data = tuple((f.name, f.getvalue()) for f in files or [])
    chat_data = tuple((f.name, f.getvalue()) for f in chat_files or [])
    if not (file_data or pasted.strip() or chat_data or chat_text.strip()):
        st.info("Noch leer – zieh oben z. B. einen Screenshot einer Anzeige hinein. Inserate, die gerade "
                "online sind, gehen noch schneller mit der Browser-Extension (Buttons *Betrug*/*Seriös* "
                "im Panel auf der Anzeige).", icon="👆")
    else:
        result = read_upload(file_data, pasted, chat_data, chat_text)
        for note in result.notes:
            st.caption(f"• {note}")
        if result.empty:
            st.warning("Daraus ließ sich kein Text lesen. Versuch einen Screenshot, die gespeicherte Seite "
                       "oder füge den Text ein.")
        else:
            sig = hashlib.sha1(repr((file_data, pasted, chat_data, chat_text)).encode()).hexdigest()[:10]
            key = f"u{n}_{sig}"
            listing = review_fields(result, key)
            if st.toggle("ScamGuard-Einschätzung vorher anzeigen", key="show_score",
                         help="Beim Einstufen besser aus lassen: Sonst beeinflusst dich das Modell und es "
                              "lernt am Ende seine eigenen Fehler. Die Einschätzung siehst du nach dem "
                              "Klick trotzdem."):
                render_result(cached_scan(listing, result.photos), compact=True)
            st.markdown("**Und – was ist es?**")
            choice = label_buttons(key)
            if choice is not None:
                total = save_labeled(listing, result.photos, choice, st.session_state[f"{key}_type"])
                verdict = cached_scan(listing, result.photos)
                st.session_state["flash"] = (
                    f"Gespeichert als **{'Betrug' if choice else 'seriös'}** – {counted(total)}. "
                    f"ScamGuard hatte {round(verdict.score * 100)} % ({verdict.verdict}) geschätzt. "
                    "Nächstes Inserat einfach hineinziehen.")
                st.session_state["upload_round"] = n + 1
                st.rerun()

# --------------------------------------------------------------------------- Felder selbst eingeben

with tab_manual:
    c1, c2, _ = st.columns([1, 1, 2])
    c1.button("Beispiel: Betrug", on_click=load_example, args=(True,))
    c2.button("Beispiel: seriös", on_click=load_example, args=(False,))

    with st.form("listing_form"):
        left, right = st.columns([2, 1])
        with left:
            st.text_input("Titel des Inserats", key="title")
            st.text_area("Beschreibung", key="description", height=180)
            st.text_area("Nachrichten des Gegenübers (optional)", key="messages", height=140,
                         help="Chatverlauf mit Anbieter oder Interessent hineinkopieren. Mehrere Nachrichten mit einer Zeile '---' trennen.")
        with right:
            st.number_input("Preis in €", key="price", min_value=0.0, step=1.0)
            st.selectbox("Kategorie", CATEGORIES, key="category",
                         format_func=lambda c: CATEGORY_LABELS.get(c, c))
            st.number_input("Kontoalter des Anbieters (Tage)", key="age", min_value=0, step=1)
            st.number_input("Anzahl Bewertungen", key="ratings", min_value=0, step=1)
            uploads = st.file_uploader("Bilder des Inserats", type=sorted(s.lstrip(".") for s in IMAGE_SUFFIXES),
                                       accept_multiple_files=True)
        submitted = st.form_submit_button("Inserat scannen", type="primary", width="stretch")

    if submitted:
        if not (st.session_state.get("title") or st.session_state.get("description") or uploads):
            st.info("Bitte mindestens Titel, Beschreibung oder ein Bild angeben.")
            st.session_state.pop("manual_scan", None)
        else:
            images = [(u.name, u.getvalue()) for u in uploads or []]
            listing = build_listing()
            with st.spinner("Analysiere …"):
                st.session_state["manual_scan"] = (listing, images, scan(listing, images))

    # Aus dem Session-State, damit die Einstufen-Buttons den nächsten Durchlauf überleben
    if manual := st.session_state.get("manual_scan"):
        listing, images, result = manual
        render_result(result)
        st.markdown("**Stimmt das? Selbst einstufen (wird zu Trainingsdaten):**")
        choice = label_buttons("manual")
        if choice is not None:
            total = save_labeled(listing, images, choice, st.session_state["manual_type"])
            st.success(f"Gespeichert als {'Betrug' if choice else 'seriös'} – {counted(total)}.")
            del st.session_state["manual_scan"]

# --------------------------------------------------------------------------- Meine Daten & Training

with tab_data:
    counts = count_labels()
    m1, m2, m3 = st.columns(3)
    m1.metric("Selbst eingestuft", counts["gesamt"])
    m2.metric("davon Betrug", counts["betrug"])
    m3.metric("davon seriös", counts["serioes"])
    st.caption(f"Gespeichert in `{LABEL_FILE}` – aus diesem Tab, der Extension und „Felder selbst eingeben“.")

    st.subheader("Wohin mit großen Mengen?")
    st.markdown(
        "Ordner einfach mit Dateien füllen – das Label ist der Unterordner (`betrug/` oder `serioes/`):\n\n"
        "| Ordner | Was gehört hinein |\n|---|---|\n"
        f"| `{LISTING_DIR}/` | ganze Inserate: Screenshots, gespeicherte Seiten, PDFs, .txt – "
        "eine Datei = ein Inserat, ein Unterordner = ein Inserat aus mehreren Dateien |\n"
        f"| `{TEXT_DIR}/` | fertige Tabellen (CSV/Excel-CSV, JSONL, Parquet) und `huggingface.yaml` |\n"
        f"| `{IMAGE_DIR}/` | nur Produktfotos (fürs Bildmodell) |")
    if sys.platform == "darwin":
        cols = st.columns(3)
        for col, (label, folder) in zip(cols, (("Inserate-Ordner", LISTING_DIR), ("Text-Ordner", TEXT_DIR),
                                              ("Bilder-Ordner", IMAGE_DIR))):
            if col.button(f"📂 {label} öffnen", key=f"open_{folder}", width="stretch"):
                subprocess.run(["open", str(resolve_path(folder))], check=False)

    specs = [s for s in get_specs(include_disabled=True) if not s.name.startswith("vorlage")]
    st.dataframe([{"Datensatz": s.name, "aktiv": "✅" if s.enabled else "–", "Hinweis": s.notes}
                  for s in specs], hide_index=True, width="stretch")

    st.subheader("Neu trainieren")
    include_demo = st.checkbox("Die 24 künstlichen Demo-Inserate mitverwenden", value=counts["gesamt"] < 20,
                               help="Nur zum Ausprobieren. Für Ergebnisse im Projektbericht weglassen – "
                                    f"sonst misst ihr Erfundenes mit (Datensatz „{DEMO_DATASET}“).")
    train_images = st.checkbox("Bildmodell mittrainieren (CNN EfficientNet – neuronales Netz)",
                               help="Braucht Produktfotos beider Klassen.")
    train_gbert = st.checkbox("GBERT mittrainieren (Transformer – neuronales Netz für Text, dauert lange)",
                              help="Lohnt sich ab einigen hundert Inseraten.")
    if st.button("🧠 Jetzt neu trainieren", type="primary"):
        with st.status("Trainiere …", expanded=True) as status:
            try:
                trained = retrain(transformer=train_gbert, images=train_images, include_demo=include_demo,
                                  log=st.write)
            except Exception as exc:  # noqa: BLE001 – im Frontend erklären statt abstürzen
                status.update(label="Training nicht möglich", state="error")
                st.error(str(exc))
            else:
                status.update(label="Fertig – die Modelle sind ab sofort aktiv", state="complete")
                fusion = trained.fusion
                st.markdown(f"**{trained.stats['total']} Beispiele** (Test: {fusion['n']}) · "
                            f"Precision {fusion['precision']:.2f} · Recall {fusion['recall']:.2f} · "
                            f"F1 {fusion['f1']:.2f}")
                st.dataframe([{"Modell": name, "n": m["n"], "Precision": round(m["precision"], 3),
                               "Recall": round(m["recall"], 3), "F1": round(m["f1"], 3),
                               "AUC": round(m["roc_auc"], 3) if "roc_auc" in m else None}
                              for name, m in trained.evaluation["models"].items()],
                             hide_index=True, width="stretch")
                if fusion["n"] < 30:
                    st.caption("Wenige Testbeispiele – die Kennzahlen schwanken noch stark.")

# --------------------------------------------------------------------------- Über

with tab_about:
    st.markdown("""
**Wie funktioniert die Erkennung?** Mehrere unabhängige Bausteine bewerten das Inserat, eine Fusion
kombiniert ihre Scores. Ein einzelnes sehr starkes Signal (z. B. ein Link auf eine nachgemachte
PayPal-Seite) hebt den Gesamtscore immer auf mindestens „hohes Risiko“.

| Baustein | Methode | Neuronales Netz? | Training |
|---|---|---|---|
| Regeln | Betrugsmaschen-Lexikon, Link-/Mail-/IBAN-Prüfung, Preis, Sprache | nein | – (Lexikon pflegt ihr) |
| Textmodell (Baseline) | TF-IDF + logistische Regression | nein, klassisches ML | mit euren Daten |
| Textmodell (GBERT) | deutsches BERT, feinjustiert | **ja** (Transformer) | mit euren Daten |
| Bildmodell | EfficientNet-B0, feinjustiert | **ja** (CNN) | mit euren Fotos |
| Bildhinweise (CLIP) | Stockfoto? Screenshot? passt das Bild zur Kategorie? | **ja**, vortrainiert | – |
| KI-Analyse (Qwen, lokal) | Sprachmodell bewertet das Inserat und erklärt warum | **ja**, vortrainiert | – |
| Texterkennung (Apple Vision) | liest Screenshots für den Upload | **ja**, vortrainiert | – |

**Eure Daten machen den Unterschied:** Die trainierbaren Bausteine sind nur so gut wie die
eingestuften Inserate. Tab „Meine Daten & Training“ zeigt, was schon da ist.
""")
