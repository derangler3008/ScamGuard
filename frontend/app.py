"""ScamGuard – Streamlit-Frontend.

Start:  scamguard ui      (oder: streamlit run frontend/app.py)
"""

from __future__ import annotations

import json
import sys
import tempfile
import uuid
from pathlib import Path

import streamlit as st

# Erlaubt `streamlit run frontend/app.py` auch ohne `pip install -e .`
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from scamguard.config import resolve_path
from scamguard.data.build import append_jsonl
from scamguard.pipeline import ScamGuard
from scamguard.schema import CATEGORIES, Listing, ScanResult

LABEL_FILE = "data/raw/eigene_labels.jsonl"
LABEL_IMAGE_DIR = "data/images/eigene_labels"
SAMPLE_FILE = "data/samples/sample_listings.jsonl"
SCAM_TYPES = ["unbekannt", "fake_zahlungslink", "vorkasse", "paypal_freunde", "dreiecksbetrug",
              "phishing", "identitaetsdiebstahl", "ueberzahlung", "fake_inserat_sonstiges"]
CATEGORY_LABELS = {
    "elektronik": "Elektronik", "haushaltsgeraete": "Haushaltsgeräte", "auto": "Auto & Fahrzeuge",
    "moebel": "Möbel", "mode": "Mode", "tiere": "Tiere", "immobilien": "Immobilien",
    "tickets": "Tickets", "sonstiges": "Sonstiges",
}

st.set_page_config(page_title="ScamGuard – Kleinanzeigen-Check", page_icon="🛡️", layout="wide")

# Startwerte einmalig setzen (Widgets lesen sie über ihren key; kein value= nötig)
for _key, _default in {"title": "", "description": "", "messages": "", "price": None,
                       "category": "sonstiges", "age": None, "ratings": None}.items():
    st.session_state.setdefault(_key, _default)


@st.cache_resource(show_spinner="Modelle werden geladen …")
def get_guard(use_llm: bool, send_images: bool) -> ScamGuard:
    return ScamGuard(overrides={"llm": {"enabled": use_llm, "send_images": send_images}})


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


def build_listing(image_paths: list[str]) -> Listing:
    raw_messages = st.session_state.get("messages") or ""
    messages = [m.strip() for m in raw_messages.split("---") if m.strip()]
    return Listing(
        title=st.session_state.get("title") or "",
        description=st.session_state.get("description") or "",
        price=st.session_state.get("price"),
        category=st.session_state.get("category") or "sonstiges",
        seller_account_age_days=st.session_state.get("age"),
        seller_num_ratings=st.session_state.get("ratings"),
        messages=messages,
        image_paths=image_paths,
    )


def render_result(result: ScanResult) -> None:
    pct = round(result.score * 100)
    if result.verdict == "hohes Risiko":
        st.error(f"**Hohes Risiko – {pct} %**  \nFinger weg: mehrere starke Betrugsmerkmale.", icon="⛔")
    elif result.verdict == "verdächtig":
        st.warning(f"**Verdächtig – {pct} %**  \nVorsicht, prüfe die Warnsignale unten genau.", icon="⚠️")
    else:
        st.success(f"**Unauffällig – {pct} %**  \nKeine deutlichen Betrugsmerkmale gefunden.", icon="✅")
    st.progress(result.score, text=f"Betrugswahrscheinlichkeit (Modell-Schätzung): {pct} %")

    st.subheader("Warnsignale")
    if not result.signals:
        st.write("Keine Warnsignale gefunden.")
    for s in result.signals:
        marker = "🔴" if s.hard or s.weight >= 0.6 else "🟠" if s.weight >= 0.3 else "🟡"
        evidence = f"  \n<small>Fundstelle: `{s.evidence}`</small>" if s.evidence else ""
        st.markdown(f"{marker} **{s.message}** · _{s.source}_{evidence}", unsafe_allow_html=True)

    with st.expander("Einzelmodelle"):
        st.dataframe([
            {"Modell": r.name,
             "Score": None if r.score is None else round(r.score, 3),
             "Status": "aktiv" if r.available and r.score is not None else "inaktiv",
             "Hinweis": r.error or ""}
            for r in result.model_results
        ], hide_index=True, width="stretch")

    with st.expander("So schützt du dich"):
        st.markdown(
            "- Bezahle nur über die Bezahlfunktion der Plattform oder bar bei Abholung.\n"
            "- Klicke keine Zahlungs- oder „Geld empfangen“-Links aus Chats oder E-Mails.\n"
            "- Schicke niemals Ausweis, Selfies oder Bestätigungscodes.\n"
            "- Kein PayPal „Freunde & Familie“ an Fremde, keine Gutscheinkarten.\n"
            "- Im Zweifel: Bilder per Rückwärtssuche prüfen und Inserat der Plattform melden."
        )


# --------------------------------------------------------------------------- Seitenleiste

with st.sidebar:
    st.header("Einstellungen")
    use_llm = st.toggle("LLM-Analyse (Claude) zuschalten", value=False,
                        help="Sendet Titel, Beschreibung und Nachrichten an die Claude API. "
                             "Verkäufername und Ort werden nicht übertragen. Kostet API-Guthaben.")
    send_images = st.toggle("Bilder an LLM senden", value=False, disabled=not use_llm)
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

tab_scan, tab_label, tab_about = st.tabs(["Inserat prüfen", "Labeln (Trainingsdaten)", "Über das Projekt"])

# --------------------------------------------------------------------------- Prüfen

with tab_scan:
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
            uploads = st.file_uploader("Bilder des Inserats", type=["jpg", "jpeg", "png", "webp"],
                                       accept_multiple_files=True)
        submitted = st.form_submit_button("Inserat scannen", type="primary", width="stretch")

    if submitted:
        if not (st.session_state.get("title") or st.session_state.get("description") or uploads):
            st.info("Bitte mindestens Titel, Beschreibung oder ein Bild angeben.")
        else:
            image_bytes = [(u.name, u.getvalue()) for u in uploads or []]
            # Bilder nur für die Dauer des Scans auf die Platte schreiben
            with tempfile.TemporaryDirectory(prefix="scamguard_ui_") as tmp:
                paths = []
                for i, (name, data) in enumerate(image_bytes):
                    p = Path(tmp) / f"{i}{Path(name).suffix.lower() or '.jpg'}"
                    p.write_bytes(data)
                    paths.append(str(p))
                listing = build_listing(paths)
                with st.spinner("Analysiere …"):
                    result = guard.scan(listing)
            st.session_state["last_scan"] = {"listing": listing, "images": image_bytes,
                                             "score": result.score}
            render_result(result)

# --------------------------------------------------------------------------- Labeln

with tab_label:
    st.write("Gescannte Inserate mit dem echten Ergebnis labeln. Sie landen in "
             f"`{LABEL_FILE}` und fließen beim nächsten `scamguard data build` ins Training ein.")
    st.caption("Datenschutz: Keine Namen, Telefonnummern oder Adressen echter Personen speichern – "
               "vorher im Text schwärzen (z. B. „[TELEFON]“).")
    last = st.session_state.get("last_scan")
    if not last:
        st.info("Erst im Tab „Inserat prüfen“ ein Inserat scannen.")
    else:
        listing: Listing = last["listing"]
        st.markdown(f"**{listing.title or '(ohne Titel)'}** · Modell-Score {last['score']:.0%}")
        with st.form("label_form"):
            verdict = st.radio("Tatsächliches Ergebnis", ["Betrug", "seriös"], horizontal=True, index=None)
            scam_type = st.selectbox("Masche (falls Betrug)", SCAM_TYPES)
            save = st.form_submit_button("In Trainingsdaten speichern", type="primary")
        if save:
            if verdict is None:
                st.warning("Bitte „Betrug“ oder „seriös“ auswählen.")
            else:
                image_dir = resolve_path(LABEL_IMAGE_DIR)
                image_dir.mkdir(parents=True, exist_ok=True)
                stored = []
                for name, data in last["images"]:
                    target = image_dir / f"{uuid.uuid4().hex}{Path(name).suffix.lower() or '.jpg'}"
                    target.write_bytes(data)
                    stored.append(str(target.relative_to(resolve_path("."))))
                labeled = Listing.from_dict({**listing.to_dict(), "image_paths": stored,
                                             "label": int(verdict == "Betrug"),
                                             "scam_type": scam_type if verdict == "Betrug" else None,
                                             "source": "eigene_labels"})
                append_jsonl(labeled, resolve_path(LABEL_FILE))
                st.success("Gespeichert.")
                del st.session_state["last_scan"]

    label_path = resolve_path(LABEL_FILE)
    if label_path.exists():
        n = sum(1 for line in label_path.read_text(encoding="utf-8").splitlines() if line.strip())
        st.metric("Selbst gelabelte Inserate", n)

# --------------------------------------------------------------------------- Über

with tab_about:
    st.markdown("""
**Wie funktioniert die Erkennung?** Mehrere unabhängige Detektoren bewerten das Inserat,
eine Fusion kombiniert ihre Scores:

| Detektor | Methode | Training nötig? |
|---|---|---|
| Regeln | Betrugsmaschen-Lexikon, URL-/Mail-/IBAN-Prüfung, Preis, Sprache | nein |
| Textmodell | TF-IDF-Baseline bzw. feingetuntes German BERT | ja |
| Bildmodell | Perceptual Hash, CLIP Zero-Shot, CNN (EfficientNet) | teilweise |
| LLM | Claude mit strukturierter Ausgabe (optional) | nein |

Ein einzelnes sehr starkes Signal (z. B. ein Link auf eine nachgemachte PayPal-Seite) hebt den
Gesamtscore immer auf mindestens „hohes Risiko“.
""")
