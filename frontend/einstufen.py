"""Tab „Einstufen im Team“ der Web-App: Annotation-Workspace für hochwertige Labels.

Texte Satz für Satz, Bilder einzeln und das Gesamturteil nach einem gemeinsamen Kategoriensystem
(data/einstufung/kategorien.yaml) einstufen, Übereinstimmung messen, Konflikte entscheiden und das
Ergebnis in die Trainingsdaten übernehmen. Die Logik steckt in src/scamguard/data/annotation.py.

Texte aus Datensätzen werden nur mit st.text angezeigt, nie als Markdown: Betrugsnachrichten
enthalten Links, die sonst anklickbar würden.
"""

from __future__ import annotations

import re
import time

import streamlit as st

from scamguard.config import resolve_path
from scamguard.data.annotation import (
    EXPORT_DIR,
    EXPORT_FILES,
    KINDS,
    MAX_SAETZE,
    Aufgabe,
    Einstufung,
    Entscheidung,
    Konsens,
    Werkstatt,
    image_urteil,
    person_key,
    regel_signale,
    saetze,
)
from scamguard.data.codebook import CODEBOOK_FILE, URTEILE, Codebook, load_codebook
from scamguard.data.registry import get_specs
from scamguard.schema import CATEGORY_NAMES, Listing

VIEWS = ["Einstufen", "Aufgaben holen", "Qualität und Konflikte", "Leitfaden"]
NAMES = {  # Anzeigenamen für Kategorien, die im Codebuch nur eine Definition haben
    "sicher": "Sicher", "eher_sicher": "Eher sicher", "unsicher": "Unsicher",
    "verkaeufer": "Verkäufer", "kaeufer": "Käufer", "unklar": "Unklar",
    "muttersprachlich": "Muttersprachlich", "leichte_fehler": "Leichte Fehler", "gebrochen": "Gebrochen",
    "maschinell_uebersetzt": "Maschinell übersetzt", "ja": "Ja", "nein": "Nein",
}


def _name(key: str | None) -> str:
    return "–" if key is None else NAMES.get(key, key.replace("_", " ").capitalize())


def _definitions(entries: dict) -> str:
    """Hilfetext eines Auswahlfelds: Name und Definition je Kategorie."""
    return "\n".join(f"- **{e['name'] if isinstance(e, dict) else _name(k)}**: "
                     f"{e['definition'] if isinstance(e, dict) else e}" for k, e in entries.items())


def _plain(text: str) -> str:
    """Werte aus geteilten Dateien in Markdown-Beschriftungen: keine Links oder Formatierung zulassen."""
    return re.sub(r"[\[\]()*_`<>#|~\\]", "", text)


def _pct(value: float | None) -> str:
    return "–" if value is None else f"{value:.0%}"


def _num(value: float | None) -> str:
    return "–" if value is None else f"{value:.2f}"


def render(werkstatt: Werkstatt | None = None) -> None:
    cb = load_codebook()
    ws = werkstatt or Werkstatt()
    person = _person()
    view = st.segmented_control("Bereich", VIEWS, default=VIEWS[0], key="ws_view",
                                label_visibility="collapsed") or VIEWS[0]
    if view == "Leitfaden":
        _leitfaden(cb)
    elif not person:
        st.info("Trag oben deinen Namen ein – jede Person stuft in eigene Dateien ein, nur so lässt sich "
                "messen, wie einig sich das Team ist.")
    elif view == "Einstufen":
        _einstufen(ws, cb, person)
    elif view == "Aufgaben holen":
        _holen(ws, person)
    else:
        _qualitaet(ws, cb, person)


def _person() -> str:
    if "ws_person" not in st.session_state:
        st.session_state["ws_person"] = st.query_params.get("person", "")
    name = st.text_input("Dein Name", key="ws_person", placeholder="z. B. Jannis",
                         help="Steht danach in der Adresszeile – als Lesezeichen gespeichert, ist er beim "
                              "nächsten Mal schon eingetragen.")
    key = person_key(name)
    if key and st.query_params.get("person") != key:
        st.query_params["person"] = key
    return key


# --------------------------------------------------------------------------- Einstufen

def _einstufen(ws: Werkstatt, cb: Codebook, person: str) -> None:
    if flash := st.session_state.pop("ws_flash", None):
        st.success(flash)
    done, tasks = ws.einstufungen(), ws.aufgaben()
    queue = ws.warteschlange(person)
    skipped = st.session_state.setdefault("ws_skipped", [])
    focus = st.session_state.get("ws_focus")
    if focus in tasks:
        current = tasks[focus]
    else:
        ordered = [t for t in queue if t.id not in skipped] + [t for t in queue if t.id in skipped]
        current = ordered[0] if ordered else None

    m1, m2, m3 = st.columns(3)
    m1.metric("Von dir eingestuft", sum(person in by for by in done.values()))
    m2.metric("Offen für dich", len(queue))
    m3.metric("Davon Zweitmeinung", sum(1 for t in queue if done.get(t.id)),
              help="Diese Texte hat schon jemand eingestuft; deine unabhängige Einstufung ergibt die "
                   "Übereinstimmung. Urteile, ohne nachzufragen, wie die andere Person entschieden hat.")
    if current is None:
        st.info("Nichts offen. Unter „Aufgaben holen“ Texte aus den Datensätzen hinzufügen.")
    else:
        _form(ws, cb, person, current, second=bool(done.get(current.id)) and focus != current.id,
              editing=focus == current.id)
    _korrigieren(ws, cb, person, tasks)


def _form(ws: Werkstatt, cb: Codebook, person: str, task: Aufgabe, second: bool, editing: bool) -> None:
    k = f"ws_{task.id}"
    st.session_state.setdefault(f"{k}_start", time.time())
    info = [task.art, f"Quelle: {_plain(task.quelle)}", f"Aufgabe {_plain(task.id[:8])}"]
    if second:
        info.append("Zweitmeinung – bitte unabhängig urteilen")
    if editing:
        info.append("Korrektur deiner Einstufung")
    st.caption(" · ".join(info))
    hints = not task.nur_bilder and st.toggle(
        "Hinweise der Regel-Erkennung zeigen", key="ws_hints",
        help="Beim Lesen besser aus: Sonst übernimmt man unbewusst, was die Regeln sagen, und der Vergleich "
             "„Regeln gegen Mensch“ misst nichts mehr. Zum Nachprüfen danach einschalten.")
    sentences = saetze(task.listing)
    rule_hits = {s.nr: regel_signale(s.text, codebook=cb) for s in sentences} if hints else {}
    images = list(zip(task.listing.image_paths, task.bilder))
    listing = task.listing

    with st.form(k):
        facts = []
        if listing.price is not None:
            facts.append(f"{listing.price:,.0f} €".replace(",", "."))
        if listing.category != "sonstiges":
            facts.append(CATEGORY_NAMES.get(listing.category, listing.category))
        if listing.seller_account_age_days is not None:
            facts.append(f"Konto {listing.seller_account_age_days} Tage alt")
        if facts:
            st.caption(" · ".join(facts))
        if sentences:
            st.caption("Satz lesen; enthält er ein Warnsignal, rechts die Art wählen.")
        part = None
        for s in sentences:
            if s.teil != part:
                st.markdown(f"**{s.teil}**")
                part = s.teil
            left, right = st.columns([3, 2], vertical_alignment="center")
            left.text(s.text)
            if rule_hits.get(s.nr):
                left.caption("Regeln: " + ", ".join(cb.name("signale", x) for x in sorted(rule_hits[s.nr])))
            right.selectbox(f"Warnsignal in Satz {s.nr}", list(cb.signale), index=None,
                            placeholder="kein Warnsignal", format_func=lambda x: cb.name("signale", x),
                            key=f"{k}_s{s.nr}", label_visibility="collapsed")
        if len(sentences) >= MAX_SAETZE:
            st.caption(f"Es werden nur die ersten {MAX_SAETZE} Sätze angezeigt.")

        for i, (path, _) in enumerate(images):
            pic, fields = st.columns([1, 2])
            file = resolve_path(path)
            if file.exists():
                pic.image(str(file), width=220)
            else:
                pic.caption("Bild fehlt auf diesem Rechner")
            fields.selectbox(f"Bild {i + 1}: Art", list(cb.bildarten), index=None, placeholder="wählen",
                             format_func=lambda a: cb.name("bildarten", a), key=f"{k}_b{i}_art",
                             help=_definitions(cb.bildarten))
            fields.segmented_control(f"Bild {i + 1}: selbst verdächtig?", list(cb.verdaechtig),
                                     format_func=_name, key=f"{k}_b{i}_v", help=_definitions(cb.verdaechtig))

        if not task.nur_bilder:
            c1, c2 = st.columns(2)
            c1.segmented_control("Urteil", list(URTEILE), format_func=lambda u: cb.urteil[u]["name"],
                                 key=f"{k}_urteil", help=_definitions(cb.urteil))
            c2.segmented_control("Wie sicher?", list(cb.sicherheit), format_func=_name, key=f"{k}_sicher",
                                 help=_definitions(cb.sicherheit))
            c3, c4, c5 = st.columns(3)
            c3.selectbox("Masche (nur bei Betrug)", list(cb.maschen), index=None, placeholder="wählen",
                         format_func=lambda m: cb.name("maschen", m), key=f"{k}_masche",
                         help="Definitionen und Beispiele: Bereich „Leitfaden“.")
            c4.selectbox("Wer täuscht? (nur bei Betrug)", list(cb.rolle), index=None, placeholder="wählen",
                         format_func=_name, key=f"{k}_rolle", help=_definitions(cb.rolle))
            c5.selectbox("Sprache", list(cb.sprache), index=None, placeholder="optional", format_func=_name,
                         key=f"{k}_sprache", help=_definitions(cb.sprache))
        st.text_input("Notiz (optional)", key=f"{k}_notiz",
                      placeholder="z. B. warum unsicher oder welche Masche bei „Sonstige“")
        submitted = st.form_submit_button("Speichern und weiter", type="primary")

    if not editing and st.button("Überspringen", key=f"{k}_skip", help="Kommt später noch einmal dran."):
        st.session_state["ws_skipped"].append(task.id)
        st.rerun()
    if editing and st.button("Korrektur abbrechen", key=f"{k}_cancel"):
        st.session_state.pop("ws_focus", None)
        st.rerun()
    if submitted:
        rating = _collect(task, person, k, sentences, images)
        if problems := ws.pruefen(rating, task):
            for problem in problems:
                st.error(problem)
            return
        ws.speichern(rating)
        saved = cb.urteil[rating.urteil]["name"]
        if rating.masche:
            saved += f" ({cb.name('maschen', rating.masche)})"
        marked = len(rating.saetze)
        message = f"Gespeichert: {saved}, {marked} {'Satz' if marked == 1 else 'Sätze'} markiert."
        if task.quelle_label is not None:
            message += f" Die Quelle hatte den Text als {'Betrug' if task.quelle_label else 'seriös'} geführt."
        st.session_state["ws_flash"] = message
        st.session_state.pop("ws_focus", None)
        st.rerun()


def _collect(task: Aufgabe, person: str, k: str, sentences, images) -> Einstufung:
    ss = st.session_state
    marks = [{"nr": s.nr, "text": s.text, "signal": ss[f"{k}_s{s.nr}"]}
             for s in sentences if ss.get(f"{k}_s{s.nr}")]
    pictures = [{"bild": h, "art": ss.get(f"{k}_b{i}_art"), "verdaechtig": ss.get(f"{k}_b{i}_v")}
                for i, (_, h) in enumerate(images) if h and (ss.get(f"{k}_b{i}_art") or ss.get(f"{k}_b{i}_v"))]
    urteil = image_urteil(pictures) if task.nur_bilder else ss.get(f"{k}_urteil")
    return Einstufung(
        aufgabe=task.id, person=person, urteil=urteil or "", sicherheit=ss.get(f"{k}_sicher"),
        masche=ss.get(f"{k}_masche"), rolle=ss.get(f"{k}_rolle"), sprache=ss.get(f"{k}_sprache"),
        saetze=marks, bilder=pictures, notiz=(ss.get(f"{k}_notiz") or "").strip(),
        dauer_s=round(time.time() - ss[f"{k}_start"], 1))


def _start_correction(rating: Einstufung, task: Aufgabe) -> None:
    """Formular mit der eigenen früheren Einstufung vorbelegen (läuft vor dem nächsten Durchlauf)."""
    k, ss = f"ws_{rating.aufgabe}", st.session_state
    ss["ws_focus"] = rating.aufgabe
    for field in ("urteil", "sicherheit", "masche", "rolle", "sprache", "notiz"):
        ss[f"{k}_{'sicher' if field == 'sicherheit' else field}"] = getattr(rating, field)
    marks = rating.signal_of()
    for s in saetze(task.listing):
        ss[f"{k}_s{s.nr}"] = marks.get(s.nr)
    by_hash = {b.get("bild"): b for b in rating.bilder}
    for i, h in enumerate(task.bilder):
        ss[f"{k}_b{i}_art"] = by_hash.get(h, {}).get("art")
        ss[f"{k}_b{i}_v"] = by_hash.get(h, {}).get("verdaechtig")
    ss[f"{k}_start"] = time.time()


def _korrigieren(ws: Werkstatt, cb: Codebook, person: str, tasks: dict[str, Aufgabe]) -> None:
    recent = [r for r in ws.meine(person) if r.aufgabe in tasks]
    if not recent:
        return
    with st.expander("Deine letzten Einstufungen korrigieren"):
        for rating in recent:
            task = tasks[rating.aufgabe]
            left, right = st.columns([4, 1], vertical_alignment="center")
            left.caption(f"{rating.zeit[:16].replace('T', ' ')} · {cb.urteil[rating.urteil]['name']}"
                         + (f" · {cb.name('maschen', rating.masche)}" if rating.masche else ""))
            left.text(task.listing.full_text[:140] or f"{len(task.listing.image_paths)} Bild(er)")
            right.button("Ändern", key=f"ws_fix_{rating.aufgabe}", on_click=_start_correction,
                         args=(rating, task))


# --------------------------------------------------------------------------- Aufgaben holen

def _holen(ws: Werkstatt, person: str) -> None:
    if flash := st.session_state.pop("ws_flash", None):
        st.success(flash)
    st.markdown("**Aus Datensätzen**")
    st.caption("Kontaktdaten (Nummern, Mails, IBANs, Links) werden beim Hinzufügen anonymisiert. Das Label "
               "der Quelle bleibt verborgen, bis du selbst geurteilt hast.")
    specs = {s.name: s for s in get_specs(include_disabled=True)
             if not s.name.startswith("vorlage") and s.name != "einstufungen"}
    names = list(specs)
    default = names.index("datei:gesammelt_foren.csv") if "datei:gesammelt_foren.csv" in names else 0
    c1, c2, c3 = st.columns([3, 1, 1], vertical_alignment="bottom")
    name = c1.selectbox("Datensatz", names, index=default,
                        format_func=lambda n: f"{n} – {specs[n].notes}"[:110] if specs[n].notes else n)
    count = c2.number_input("Anzahl", min_value=1, max_value=500, value=50, step=10)
    balanced = c3.checkbox("ausgewogen", value=True,
                           help="Gleich viele Texte je Label der Quelle (z. B. Spam und kein Spam), damit nicht "
                                "nur eine Klasse eingestuft wird.")
    if st.button("Hinzufügen", type="primary"):
        with st.spinner("Lese den Datensatz …"):
            try:
                new, existing = ws.hinzufuegen_aus_datensatz(specs[name], person, int(count), balanced)
            except Exception as exc:  # noqa: BLE001 – im Frontend erklären statt abstürzen
                st.error(f"Konnte {name} nicht lesen: {exc}")
            else:
                st.success(f"{new} neue Aufgaben hinzugefügt"
                           + (f", {existing} waren schon da." if existing else "."))

    st.markdown("**Eigenen Text hinzufügen**")
    own_round = st.session_state.setdefault("ws_own_round", 0)  # neue Runde = leeres Formular
    with st.form(f"ws_own_{own_round}"):  # nicht clear_on_submit: bei Fehlern bliebe der Text sonst nicht stehen
        text = st.text_area("Text eines Inserats oder Chats", height=140,
                            help="Mehrere Chat-Nachrichten mit einer Zeile „---“ trennen.")
        kind = st.radio("Art", ["Chat", "Inserat"], horizontal=True)
        consent = st.checkbox("Der Text stammt von mir, oder die beteiligten Personen haben der Nutzung im "
                              "Studienprojekt zugestimmt.")
        st.caption("Nummern, Mailadressen, IBANs und Links werden automatisch anonymisiert. Namen im Text "
                   "bitte selbst entfernen.")
        if st.form_submit_button("Als Aufgabe hinzufügen"):
            if not text.strip():
                st.error("Bitte einen Text einfügen.")
            elif not consent:
                st.error("Ohne Zustimmung der Beteiligten bitte nicht verwenden.")
            else:
                parts = [p.strip() for p in text.split("---") if p.strip()]
                listing = Listing(messages=parts) if kind == "Chat" else Listing(description=text.strip())
                new, _ = ws.hinzufuegen([listing], "eigene_eingabe", person)
                st.session_state["ws_flash"] = ("Als Aufgabe hinzugefügt (Kontaktdaten anonymisiert)." if new
                                                else "Diesen Text gibt es schon als Aufgabe.")
                st.session_state["ws_own_round"] = own_round + 1
                st.rerun()

    st.markdown("**Mit dem Team teilen**")
    st.caption(f"Arbeitsordner: `{ws.folder}`. Am einfachsten arbeiten alle im selben privaten Ordner "
               "(z. B. DHBW-OneDrive): Web-App mit `SCAMGUARD_EINSTUFUNG_ORDNER=<Pfad> scamguard ui` starten – "
               "jede Person schreibt nur eigene Dateien. Sonst hier die eigenen Dateien herunterladen und die "
               "der anderen hinzufügen.")
    own = [(kind, ws.folder / f"{kind}_{person}.jsonl") for kind in KINDS]
    columns = st.columns(len(own))
    for col, (kind, path) in zip(columns, own):
        if path.exists():
            col.download_button(f"Meine {kind}", path.read_bytes(), file_name=path.name,
                                mime="application/jsonl", key=f"ws_dl_{kind}")
    round_ = st.session_state.setdefault("ws_upload_round", 0)
    uploads = st.file_uploader("Dateien aus dem Team hinzufügen (aufgaben_…, einstufungen_…, entscheidungen_….jsonl)",
                               type=["jsonl"], accept_multiple_files=True, key=f"ws_upload_{round_}")
    if uploads and st.button("Übernehmen"):
        messages = []
        for upload in uploads:
            try:
                added = ws.zusammenfuehren(upload.name, upload.getvalue(), person)
            except ValueError as exc:
                messages.append(f"{upload.name}: {exc}")
            else:
                messages.append(f"{upload.name}: {added} neue Zeilen")
        st.session_state["ws_flash"] = " · ".join(messages)
        st.session_state["ws_upload_round"] = round_ + 1
        st.rerun()


# --------------------------------------------------------------------------- Qualität

def _qualitaet(ws: Werkstatt, cb: Codebook, person: str) -> None:
    if flash := st.session_state.pop("ws_flash", None):
        st.success(flash)
    report = ws.bericht()
    m = st.columns(4)
    m[0].metric("Aufgaben", report["aufgaben"])
    m[1].metric("Eingestuft", report["eingestuft"])
    m[2].metric("Von zwei oder mehr", report["doppelt_eingestuft"])
    m[3].metric("Konflikte", len(report["konflikte"]))
    if report["personen"]:
        st.caption("Je Person: " + " · ".join(f"{p} {n}" for p, n in sorted(report["personen"].items())))

    st.subheader("Übereinstimmung")
    st.dataframe([{"Ebene": r["ebene"], "Gemeinsam eingestuft": r["einheiten"],
                   "Krippendorffs α": _num(r["alpha"]), "Gleich geurteilt": _pct(r["prozent"]),
                   "Einschätzung": r["einschaetzung"]} for r in report["uebereinstimmung"]],
                 hide_index=True, width="stretch")
    st.caption("α ≥ 0,80 verlässlich · ≥ 0,667 vorläufig verwendbar · darunter Leitfaden schärfen und neu "
               "messen (Krippendorff 2004). „Unklar“ zählt beim Urteil nicht mit.")
    if report["kappa"]:
        st.dataframe([{"Personen": r["paar"], "Gemeinsame Texte": r["n"], "Cohens κ": _num(r["kappa"])}
                      for r in report["kappa"]], hide_index=True)

    st.subheader("Konflikte")
    konsens = ws.konsens()
    if not report["konflikte"]:
        st.caption("Keine – alle mehrfach eingestuften Texte haben eine Mehrheit.")
    for aid in report["konflikte"][:20]:
        _konflikt(ws, cb, person, konsens[aid])

    if report["auffaelligkeiten"]:
        st.subheader("Auffälligkeiten")
        st.dataframe([{"Aufgabe": a["aufgabe"][:8], "Person": a["person"], "Hinweis": a["hinweis"]}
                      for a in report["auffaelligkeiten"]], hide_index=True, width="stretch")
    if report["quellen"]:
        st.subheader("Wie verlässlich sind die Labels der Quellen?")
        st.dataframe([{"Quelle": q["quelle"], "Eingestuft": q["n"], "Widerspricht dem Konsens": q["widerspruch"],
                       "Anteil": _pct(q["widerspruch"] / q["n"])} for q in report["quellen"]],
                     hide_index=True, width="stretch")

    st.subheader("Regeln gegen Mensch")
    st.caption("Vergleicht Satz für Satz die Regel-Erkennung mit den Markierungen im Team: Wo schlägt sie falsch an, "
               "was verpasst sie? Verpasste Sätze sind Kandidaten für neue Lexikon-Muster.")
    if st.button("Regeln satzgenau prüfen"):
        with st.spinner("Prüfe jeden Satz …"):
            check = ws.regeln_gegen_mensch()
        overall = check["gesamt"]
        st.markdown(f"{check['saetze']} Sätze · irgendein Warnsignal erkannt: Precision {_pct(overall['precision'])}, "
                    f"Recall {_pct(overall['recall'])}")
        st.dataframe([{"Warnsignal": r["name"], "Richtig": r["tp"], "Fehlalarm": r["fp"], "Verpasst": r["fn"],
                       "Precision": _pct(r["precision"]), "Recall": _pct(r["recall"])} for r in check["je_signal"]],
                     hide_index=True, width="stretch")
        for title, rows in (("Verpasst (Mensch ja, Regeln nein)", check["verpasst"]),
                            ("Fehlalarme (Regeln ja, Mensch nein)", check["fehlalarme"])):
            if rows:
                with st.expander(f"{title}: {len(rows)} Beispiele"):
                    for row in rows:
                        st.caption(cb.name("signale", row["signal"]) if row["signal"] in cb.signale else row["signal"])
                        st.text(row["text"])

    st.subheader("In die Trainingsdaten übernehmen")
    st.caption(f"Schreibt den Konsens nach `{EXPORT_DIR}/` ({', '.join(EXPORT_FILES.values())}). Konflikte und "
               "„unklar“ bleiben draußen. Danach `scamguard retrain` bzw. „Jetzt neu trainieren“ und "
               "`scamguard llm-daten`.")
    if st.button("Exportieren", type="primary"):
        counts = ws.export()
        st.success(f"{counts['konsens']} Texte, {counts['saetze']} Sätze und {counts['bilder']} Bilder exportiert.")


def _konflikt(ws: Werkstatt, cb: Codebook, person: str, k: Konsens) -> None:
    aid = k.aufgabe.id
    votes = ", ".join(f"{e.person}: {cb.urteil[e.urteil]['name']}" for e in k.einstufungen)
    with st.expander(f"Aufgabe {_plain(aid[:8])} – {votes}"):
        st.text(k.aufgabe.listing.full_text[:3000])
        st.dataframe([{"Person": e.person, "Urteil": cb.urteil[e.urteil]["name"], "Sicherheit": _name(e.sicherheit),
                       "Masche": cb.name("maschen", e.masche), "Notiz": e.notiz} for e in k.einstufungen],
                     hide_index=True, width="stretch")
        with st.form(f"ws_decide_{aid}"):
            urteil = st.segmented_control("Entscheidung", list(URTEILE), format_func=lambda u: cb.urteil[u]["name"],
                                          key=f"ws_decide_{aid}_urteil")
            masche = st.selectbox("Masche (bei Betrug)", list(cb.maschen), index=None, placeholder="wählen",
                                  format_func=lambda m: cb.name("maschen", m), key=f"ws_decide_{aid}_masche")
            reason = st.text_input("Begründung (wird mitgespeichert)", key=f"ws_decide_{aid}_grund")
            if st.form_submit_button("Entscheidung speichern"):
                if not urteil:
                    st.error("Bitte eine Entscheidung wählen.")
                elif urteil == "betrug" and not masche:
                    st.error("Bei Betrug bitte die Masche wählen.")
                else:
                    ws.entscheiden(Entscheidung(aid, person, urteil, masche, reason.strip()))
                    st.session_state["ws_flash"] = f"Entscheidung zu Aufgabe {_plain(aid[:8])} gespeichert."
                    st.rerun()


# --------------------------------------------------------------------------- Leitfaden

def _leitfaden(cb: Codebook) -> None:
    st.caption(f"Fassung {cb.version} · `{CODEBOOK_FILE}` · Entwurf – im Team besprechen und anpassen; danach "
               "die Fassung erhöhen.")
    st.subheader("Entscheidungsregeln")
    st.markdown("\n".join(f"{i}. {rule}" for i, rule in enumerate(cb.regeln, 1)))
    st.subheader("Urteil")
    st.markdown(_definitions(cb.urteil))
    st.subheader("Maschen (nur bei Betrug)")
    for entry in cb.maschen.values():
        lines = [f"**{entry['name']}** – {entry['definition']}"]
        if entry.get("beispiel"):
            lines.append(f"Beispiel: _„{entry['beispiel']}“_")
        if entry.get("abgrenzung"):
            lines.append(f"Abgrenzung: {entry['abgrenzung']}")
        st.markdown("  \n".join(lines))
    st.subheader("Warnsignale auf Satzebene")
    for entry in cb.signale.values():
        example = f"  \nBeispiel: _„{entry['beispiel']}“_" if entry.get("beispiel") else ""
        st.markdown(f"**{entry['name']}** – {entry['definition']}{example}")
    st.subheader("Bilder")
    st.markdown(_definitions(cb.bildarten))
    st.markdown("**Selbst verdächtig?**\n" + _definitions(cb.verdaechtig))
    st.subheader("Wer täuscht, Sprache, Sicherheit")
    st.markdown(_definitions(cb.rolle) + "\n\n" + _definitions(cb.sprache) + "\n\n" + _definitions(cb.sicherheit))
