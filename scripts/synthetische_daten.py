"""Synthetische Trainingsdaten erzeugen – damit Pipeline, Texterkennung und Modelle schon vor dem
eigenen Labeling etwas zu tun haben.

    python scripts/synthetische_daten.py chats --anzahl 200          # nachgebaute Chat-Screenshots
    python scripts/synthetische_daten.py qwen-chats --anzahl 100     # Gespräche von Qwen geschrieben
    .venv-lora/Scripts/python scripts/synthetische_daten.py fotos --anzahl 60   # KI-Produktfotos (GPU)

Ablage (nicht im Git): data/datensatz_fuellen_inserate/synthetisch/{betrug,serioes}/<id>/
(inserat.txt + chat_1.png) bzw. data/datensatz_fuellen_bilder/synthetisch/{betrug,serioes}/*.png.
Der Ordner „synthetisch“ ist eine eigene Quelle und landet beim Bauen der Splits nur im Training, nie in
Validierung oder Test (discovery.py) – Kennzahlen bleiben so ehrlich.

- Chats: typische Maschen (Fake-Zahlungslink, „Freunde & Familie“, Kurier/Spedition, Code-Weitergabe,
  WhatsApp-Wechsel, Ausweis, Tier-Transport, Mietkaution) und gewöhnliche Gespräche (Abholung,
  Preisverhandlung, Versand über die Plattform). Kontaktdaten sind Platzhalter, Namen erfunden.
- Fotos: „betrug“ = makellose Katalog-/Stockfotos, wie Fake-Inserate sie nutzen; „seriös“ = gewöhnliche
  Handyfotos zu Hause. Beides KI-generiert – das Bildmodell lernt daran nur den Stil, echte Fotos aus
  eigenen Labels bleiben nötig.
"""

from __future__ import annotations

import argparse
import hashlib
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LISTING_DIR = ROOT / "data" / "datensatz_fuellen_inserate" / "synthetisch"
IMAGE_DIR = ROOT / "data" / "datensatz_fuellen_bilder" / "synthetisch"

# Artikel: (Titel, Kategorie-Pfad, üblicher Preis, Beschreibung)
ITEMS = [
    ("iPhone 14 Pro 128 GB", "Elektronik › Handy & Telefon", 650, "Gebraucht, Akku 88 %, keine Kratzer. Mit OVP."),
    ("PlayStation 5 Disc Edition", "Elektronik › Konsolen", 380, "Mit zwei Controllern, funktioniert einwandfrei."),
    ("MacBook Air M2", "Elektronik › Notebooks", 820, "13 Zoll, 8 GB, 256 GB. Kaum benutzt."),
    ("Nintendo Switch OLED", "Elektronik › Konsolen", 240, "Weiß, mit Hülle und zwei Spielen."),
    ("Thermomix TM6", "Haus & Garten › Küche", 750, "Voll funktionsfähig, mit Zubehör."),
    ("E-Bike Cube Reaction", "Fahrräder › Elektrofahrräder", 1900, "Rahmen L, Akku 625 Wh, regelmäßig gewartet."),
    ("Dyson V15 Detect", "Haus & Garten › Haushaltsgeräte", 380, "Mit allen Aufsätzen, Akku hält lange."),
    ("Kinderwagen Cybex Priam", "Familie, Kind & Baby › Kinderwagen", 450, "Mit Babywanne und Sportsitz."),
    ("Grafikkarte RTX 4070", "Elektronik › PC-Zubehör", 470, "Nie übertaktet, Rechnung vorhanden."),
    ("Sofa 3-Sitzer grau", "Haus & Garten › Wohnzimmer", 200, "Nichtraucherhaushalt, Selbstabholung."),
    ("Golden Retriever Welpe", "Haustiere › Hunde", 1500, "Geimpft, gechippt, mit EU-Heimtierausweis."),
    ("2-Zimmer-Wohnung, 55 m²", "Immobilien › Mietwohnungen", 780, "Warmmiete, ab sofort frei, Balkon."),
]
CITIES = ["Mannheim", "Heidelberg", "Karlsruhe", "Stuttgart", "Frankfurt", "Köln", "Leipzig", "Hamburg"]
NAMES = ["Max", "Erika", "Jonas", "Lena", "Tim", "Sarah", "Paul", "Anna", "Ben", "Mia"]
FAKE_LINK = ["https://kleinanzeigen-de.sicher-bezahlen.top/empfang", "https://kleinanzeigen.secure-pay24.shop/k/8812",
             "https://kleinanzeigen-zahlung.info/bestaetigen"]
FAKE_PHONE = ["+44 1111 111111", "+48 111 111 111", "+33 1 11 11 11 11"]

# Gespräche: Liste von (wer, Text). "ich" = Kontoinhaber (rechts), "sie" = Gegenüber (links).
# {item}, {price}, {low}, {name}, {city}, {link}, {phone} werden eingesetzt.
SCAM_SELLER_SIDE = [  # ich verkaufe, „Käufer“ betrügt
    [("sie", "Hallo, ist der Artikel noch verfügbar?"), ("ich", "Hallo, ja ist noch da."),
     ("sie", "Super, ich nehme ihn zum vollen Preis. Ich habe die Zahlung über Sicher bezahlen schon veranlasst."),
     ("sie", "Bitte bestätigen Sie den Zahlungseingang über diesen Link, dann wird das Geld freigegeben: {link}"),
     ("ich", "Ich sehe keine Zahlung in der App?"),
     ("sie", "Das ist neu, Sie müssen nur Ihre Kartendaten zur Verifizierung eingeben. Bitte schnell, sonst verfällt die Zahlung.")],
    [("sie", "Guten Tag, ich möchte {item} kaufen. Ich bin zurzeit im Ausland auf Montage."),
     ("sie", "Ein Kurier von DHL holt den Artikel bei Ihnen ab, ich überweise {price} € plus 50 € für den Spediteur."),
     ("ich", "Warum mehr als der Preis?"),
     ("sie", "Bitte überweisen Sie die 50 € an den Spediteur weiter, die Kontodaten schicke ich Ihnen. Der Kurier kommt morgen.")],
    [("sie", "Hallo {name}, ist {item} noch zu haben?"), ("ich", "Ja"),
     ("sie", "Ich bezahle per Sicher bezahlen. Schreib mir deine Handynummer, dann bekommst du eine SMS mit einem Code."),
     ("ich", "Ok, ich habe einen Code bekommen."),
     ("sie", "Schick mir bitte den Code aus der SMS, damit ich die Zahlung abschließen kann. Ist ganz normal so.")],
    [("sie", "Hi, Artikel noch da? Ich kaufe sofort."),
     ("sie", "Bitte schreib mir auf WhatsApp, hier antworte ich nicht so schnell: {phone}"),
     ("ich", "Können wir nicht hier schreiben?"),
     ("sie", "Nein bitte WhatsApp, die App funktioniert bei mir nicht. Meine Bank schickt dir dann den Link.")],
    [("sie", "Hallo, ich habe aus Versehen 300 € zu viel überwiesen."),
     ("sie", "Bitte schicken Sie mir das Geld per PayPal Freunde und Familie zurück, Sie können das Geld nicht behalten."),
     ("ich", "Bei mir ist noch gar nichts angekommen."),
     ("sie", "Die Bank zeigt das erst später an. Bitte heute noch zurückschicken, sonst muss ich die Polizei einschalten.")],
]
SCAM_BUYER_SIDE = [  # ich kaufe, „Verkäufer“ betrügt
    [("ich", "Hallo, ist {item} noch da? Kann ich es in {city} abholen?"),
     ("sie", "Hallo, ja noch da. Abholung geht leider nicht, ich bin zurzeit im Ausland."),
     ("sie", "Ich versende mit DHL. Zahlung bitte per PayPal Freunde und Familie, dann spare ich die Gebühren."),
     ("ich", "Geht auch Waren und Dienstleistungen?"),
     ("sie", "Nein leider nicht. Es gibt viele Interessenten, wenn du heute zahlst, reserviere ich für dich.")],
    [("ich", "Guten Tag, ist der Preis von {price} € fest?"),
     ("sie", "Für Sie {low} €. Bitte überweisen Sie vorab, nach Zahlungseingang versende ich sofort."),
     ("ich", "Können wir Sicher bezahlen nutzen?"),
     ("sie", "Das funktioniert bei mir nicht. Hier meine IBAN: LT11 1111 1111 1111 1111. Schnell sein, sonst ist es weg.")],
    [("ich", "Hallo, ich hätte Interesse an {item}."),
     ("sie", "Gerne. Bevor ich versende, brauche ich ein Foto von Ihrem Personalausweis, Vorder- und Rückseite."),
     ("sie", "Das ist wegen der Sicherheit, es gab viele Betrüger. Danach schicke ich Ihnen den Bezahllink: {link}")],
    [("ich", "Hallo, ist der Welpe noch zu haben?"),
     ("sie", "Ja, er ist noch da. Wir sind umgezogen, der Welpe ist bei einer Tierspedition."),
     ("sie", "Sie zahlen nur die Transportgebühr von 250 € vorab, den Rest bei Übergabe. Bitte per Western Union."),
     ("ich", "Kann ich ihn vorher ansehen?"), ("sie", "Leider nicht möglich, der Transport ist schon gebucht.")],
    [("ich", "Hallo, ist die Wohnung noch frei? Wann kann ich sie besichtigen?"),
     ("sie", "Ich wohne derzeit in London. Die Schlüssel schicke ich nach Zahlung der Kaution von 1.500 €."),
     ("sie", "Bitte überweisen Sie über diesen Treuhand-Link: {link}. Danach versende ich den Mietvertrag.")],
]
LEGIT = [
    [("sie", "Hallo, ist {item} noch verfügbar?"), ("ich", "Hallo, ja ist noch da."),
     ("sie", "Kann ich morgen um 18 Uhr vorbeikommen und es mir anschauen?"),
     ("ich", "Ja passt, ich schicke dir die Adresse."), ("sie", "Super, danke. Ich zahle dann bar.")],
    [("ich", "Hallo, wäre {low} € möglich?"), ("sie", "Hallo, {price} € wäre mir lieber, aber für {low} € gebe ich es dir, wenn du abholst."),
     ("ich", "Deal. Samstag Vormittag ginge bei mir."), ("sie", "Samstag 11 Uhr passt. Bis dann!")],
    [("sie", "Hi, verschickst du auch?"),
     ("ich", "Ja, über Sicher bezahlen hier in der App, Versand mit DHL für 6,99 €."),
     ("sie", "Okay, ich schicke dir gleich die Anfrage über die App."), ("ich", "Danke, versende morgen früh.")],
    [("ich", "Hallo, gibt es noch die Rechnung zu {item}?"),
     ("sie", "Ja, die Rechnung ist dabei, Garantie läuft noch bis März."),
     ("ich", "Gibt es Kratzer oder Mängel?"), ("sie", "Eine kleine Macke an der Seite, sieht man auf dem dritten Foto."),
     ("ich", "Alles klar, ich überlege es mir und melde mich.")],
    [("sie", "Hallo, ist das noch da?"), ("ich", "Hallo, leider gerade reserviert, ich melde mich, falls es frei wird."),
     ("sie", "Okay, danke für die Info!")],
    [("ich", "Hallo {name}, ich bin jetzt da, stehe vor dem Haus."), ("sie", "Komme runter!"),
     ("ich", "Hat alles geklappt, danke nochmal."), ("sie", "Gerne, viel Spaß damit!")],
]


def _font(size: int):
    from PIL import ImageFont

    for name in ("arial.ttf", "/System/Library/Fonts/Helvetica.ttc", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size)


def _wrap(draw, text: str, font, width: int) -> list[str]:
    lines, line = [], ""
    for word in text.split():
        candidate = f"{line} {word}".strip()
        if draw.textlength(candidate, font=font) <= width:
            line = candidate
        else:
            lines.append(line)
            line = word
    return lines + [line] if line else lines


def render_chat(title: str, price: str, partner: str, messages: list[tuple[str, str]], rng: random.Random):
    """Chat-Screenshot im Stil des Kleinanzeigen-Postfachs (Handy, 720 px breit)."""
    from PIL import Image, ImageDraw

    width, pad, bubble_w = 720, 24, 500
    text_font, small, bold = _font(26), _font(20), _font(28)
    probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    layout = []
    for who, text in messages:
        lines = _wrap(probe, text, text_font, bubble_w - 2 * pad)
        layout.append((who, lines, 34 * len(lines) + 2 * 18 + 26))
    height = 200 + sum(h + 22 for _, _, h in layout) + 40
    img = Image.new("RGB", (width, height), "#ffffff")
    draw = ImageDraw.Draw(img)
    draw.rectangle((0, 0, width, 70), fill="#1d4b00" if rng.random() < 0.5 else "#326916")
    draw.text((pad, 20), partner, font=bold, fill="white")
    draw.rectangle((0, 70, width, 170), fill="#f4f4f4")
    draw.rounded_rectangle((pad, 85, pad + 70, 155), 8, fill=(rng.randint(150, 220),) * 3)
    draw.text((pad + 90, 92), title, font=bold, fill="#222222")
    draw.text((pad + 90, 128), price, font=text_font, fill="#326916")
    y, hour, minute = 200, rng.randint(8, 21), rng.randint(0, 50)
    for who, lines, h in layout:
        mine = who == "ich"
        w = max(max(probe.textlength(line, font=text_font) for line in lines), 70) + 2 * pad  # Platz für die Uhrzeit
        x = width - pad - w if mine else pad
        draw.rounded_rectangle((x, y, x + w, y + h), 18, fill="#dcf3c8" if mine else "#eeeeee")
        for i, line in enumerate(lines):
            draw.text((x + pad, y + 16 + 34 * i), line, font=text_font, fill="#111111")
        minute = min(59, minute + rng.randint(0, 4))
        stamp = f"{hour:02d}:{minute:02d}"
        draw.text((x + w - pad - probe.textlength(stamp, font=small), y + h - 30), stamp, font=small, fill="#777777")
        y += h + 22
    return img


def _fill(text: str, values: dict) -> str:
    return text.format(**values)


def _write_example(label: str, name: str, item: tuple, price: int, city: str, messages: list[tuple[str, str]],
                   new_account: bool, rng: random.Random) -> None:
    """Ein Beispiel ablegen: inserat.txt (Inseratstext) + chat_1.png (Screenshot)."""
    title, path, _, desc = item
    folder = LISTING_DIR / label / name
    folder.mkdir(parents=True, exist_ok=True)
    age = rng.randint(1, 20) if new_account else rng.randint(200, 3000)
    (folder / "inserat.txt").write_text(
        f"Kleinanzeigen {city} › {path}\n{title}\n{price} € VB\n{city}\nBeschreibung\n{desc}\n"
        f"Privater Nutzer\nAktiv seit {age} Tagen\n", encoding="utf-8")
    render_chat(title, f"{price} €", rng.choice(NAMES), messages, rng).save(folder / "chat_1.png")


def cmd_chats(args) -> int:
    rng = random.Random(args.seed)
    written = {"betrug": 0, "serioes": 0}
    for n in range(args.anzahl):
        scam = rng.random() < args.anteil_betrug
        if scam:
            seller_side = rng.random() < 0.5
            script = rng.choice(SCAM_SELLER_SIDE if seller_side else SCAM_BUYER_SIDE)
        else:
            seller_side, script = rng.random() < 0.5, rng.choice(LEGIT)
        title, path, base, desc = rng.choice(ITEMS)
        if any("Welpe" in t for _, t in script):
            title, path, base, desc = ITEMS[10]
        if any("Wohnung" in t for _, t in script):
            title, path, base, desc = ITEMS[11]
        price = int(base * (rng.uniform(0.35, 0.6) if scam and not seller_side else rng.uniform(0.85, 1.1)))
        values = {"item": title, "price": price, "low": int(price * 0.9), "name": rng.choice(NAMES),
                  "city": rng.choice(CITIES), "link": rng.choice(FAKE_LINK), "phone": rng.choice(FAKE_PHONE)}
        messages = [(who, _fill(text, values)) for who, text in script]
        label = "betrug" if scam else "serioes"
        _write_example(label, f"chat_{hashlib.sha1(f'{args.seed}-{n}'.encode()).hexdigest()[:10]}",
                       (title, path, base, desc), price, values["city"], messages,
                       new_account=scam and not seller_side, rng=rng)
        written[label] += 1
    print(f"{sum(written.values())} Chats erzeugt ({written['betrug']} Betrug, {written['serioes']} seriös) "
          f"→ {LISTING_DIR}")
    print("Nur Training (Quelle „synthetisch“). Weiter: scamguard retrain")
    return 0


# Szenarien für Qwen: (Label, Rolle des Kontoinhabers, Masche bzw. Gesprächsart)
QWEN_SCENARIOS = [
    ("betrug", "verkauft", ("Käufer schickt angeblichen „Sicher bezahlen“-Link einer gefälschten Seite und drängt, "
                           "den Zahlungseingang dort mit Kartendaten zu bestätigen")),
    ("betrug", "verkauft", ("Käufer ist angeblich im Ausland, schickt einen Kurier und will, dass der Verkäufer "
                           "Spediteurkosten vorab weiterleitet")),
    ("betrug", "verkauft", "Käufer will die Handynummer und danach den SMS-Code „für die Zahlung“"),
    ("betrug", "verkauft", "Käufer behauptet Überzahlung und verlangt Rücküberweisung per PayPal Freunde und Familie"),
    ("betrug", "verkauft", "Käufer will sofort zu WhatsApp wechseln und schickt dort einen Bezahllink"),
    ("betrug", "kauft", "Verkäufer ist angeblich im Ausland, nur Versand, nur PayPal Freunde und Familie, Zeitdruck"),
    ("betrug", "kauft", "Verkäufer verlangt Vorkasse per Überweisung auf ausländische IBAN, Preis auffällig günstig"),
    ("betrug", "kauft", "Verkäufer verlangt vorab ein Foto des Personalausweises „wegen Sicherheit“"),
    ("betrug", "kauft", "Tierverkauf: Welpe ist bei einer Tierspedition, Transportgebühr vorab per Western Union"),
    ("betrug", "kauft", "Wohnungsanzeige: Vermieter im Ausland, Kaution vorab, Schlüssel per Post"),
    ("betrug", "kauft", ("Dreiecksbetrug: Verkäufer gibt Bankdaten einer fremden Person an und drängt zur schnellen "
                        "Überweisung")),
    ("serioes", "verkauft", "Interessent fragt, ob der Artikel noch da ist, und vereinbart Abholung mit Barzahlung"),
    ("serioes", "kauft", "Preisverhandlung, Einigung auf etwas weniger, Abholung am Wochenende"),
    ("serioes", "verkauft", "Versand über die offizielle Bezahlfunktion in der App, Fragen zu Versandkosten"),
    ("serioes", "kauft", "Fragen zu Zustand, Rechnung und Garantie, höfliche Antwort mit Details"),
    ("serioes", "verkauft", "Artikel ist schon reserviert, freundliche Absage"),
    ("serioes", "kauft", "Terminabsprache zur Besichtigung, kurze Rückfragen zur Adresse"),
    ("serioes", "verkauft", "Käufer kommt zu spät, entschuldigt sich, Übergabe klappt"),
]
QWEN_PROMPT = """Schreibe einen realistischen Chatverlauf aus dem Postfach von Kleinanzeigen (Deutschland).
Artikel: {item}, angeboten für {price} €.
Der Kontoinhaber ({role} den Artikel) ist „ich“, das Gegenüber ist „sie“.
Gesprächsart: {scenario}
{style}
Regeln: 4 bis 9 Nachrichten, kurze Chat-Nachrichten wie echte Menschen sie schreiben (umgangssprachlich,
gelegentlich Tippfehler, mal ohne Satzzeichen). Keine echten Namen, Telefonnummern oder Adressen:
Telefonnummern nur +44 1111 111111, Mailadressen nur anonym@gmail.com, Links nur auf erfundene Domains mit
.top, .shop oder .info.
Antworte nur mit JSON: {{"nachrichten": [{{"wer": "ich" oder "sie", "text": "..."}}]}}"""
STYLES = {
    "betrug": ["Das Gegenüber schreibt holpriges, leicht maschinell übersetztes Deutsch.",
               "Das Gegenüber schreibt freundlich und fehlerfrei, wirkt seriös.",
               "Das Gegenüber schreibt knapp und drängelnd."],
    "serioes": ["Beide schreiben locker und kurz.", "Das Gegenüber ist sehr höflich und siezt.",
                "Das Gegenüber ist kein Muttersprachler und macht Grammatikfehler, ist aber ehrlich."],
}
QWEN_SCHEMA = {"type": "object", "required": ["nachrichten"], "properties": {"nachrichten": {
    "type": "array", "minItems": 3, "maxItems": 10, "items": {
        "type": "object", "required": ["wer", "text"],
        "properties": {"wer": {"type": "string", "enum": ["ich", "sie"]}, "text": {"type": "string"}}}}}}


def cmd_qwen_chats(args) -> int:
    """Wie `chats`, aber die Gespräche schreibt Qwen (lokaler Server aus config.yaml) – viel vielfältiger."""
    import json
    from concurrent.futures import ThreadPoolExecutor, as_completed

    import httpx

    from scamguard.config import load_config
    from scamguard.data.redact import redact

    local = load_config()["llm"]["local"]
    base = local["base_url"].rstrip("/")
    try:
        httpx.get(f"{base}/models", timeout=3).raise_for_status()
    except httpx.HTTPError:
        sys.exit(f"Qwen läuft nicht ({base}) – vorher `scamguard llm-server` bzw. `scamguard start`.")
    rng = random.Random(args.seed)
    tasks = []  # erst alle Aufträge festlegen (reproduzierbar), dann parallel an Qwen schicken
    for n in range(args.anzahl):
        want = "betrug" if rng.random() < args.anteil_betrug else "serioes"
        label, role, scenario = rng.choice([s for s in QWEN_SCENARIOS if s[0] == want])
        item = ITEMS[10] if "Welpe" in scenario else ITEMS[11] if "Wohnung" in scenario else rng.choice(ITEMS[:10])
        cheap = label == "betrug" and role == "kauft"
        price = int(item[2] * (rng.uniform(0.35, 0.6) if cheap else rng.uniform(0.85, 1.1)))
        prompt = QWEN_PROMPT.format(item=item[0], price=price, role=role, scenario=scenario,
                                    style=rng.choice(STYLES[label]))
        tasks.append((n, label, scenario, item, price, cheap, prompt))

    def ask(prompt: str) -> list[dict]:
        reply = httpx.post(f"{base}/chat/completions", timeout=600, json={
            "model": local["model"], "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.9, "top_p": 0.95, "max_tokens": 900,
            "chat_template_kwargs": {"enable_thinking": False},
            "response_format": {"type": "json_schema", "json_schema": {
                "name": "chat", "schema": QWEN_SCHEMA, "strict": True}}}).json()
        return json.loads(reply["choices"][0]["message"]["content"])["nachrichten"]

    # Mehrere Anfragen gleichzeitig: llama.cpp rechnet sie gemeinsam (Batching) – die GPU ist so viel
    # besser ausgelastet als mit einer Anfrage nach der anderen
    written, done = {"betrug": 0, "serioes": 0}, 0
    with ThreadPoolExecutor(max_workers=args.parallel) as pool:
        futures = {pool.submit(ask, task[-1]): task for task in tasks}
        for future in as_completed(futures):
            n, label, scenario, item, price, cheap, _ = futures[future]
            done += 1
            try:
                chat = future.result()
            except (httpx.HTTPError, KeyError, ValueError) as exc:
                print(f"  {done:3d}/{args.anzahl}  übersprungen ({exc.__class__.__name__})", flush=True)
                continue
            messages = [(m["wer"], redact(m["text"].strip())) for m in chat if m.get("text", "").strip()]
            if len(messages) < 3:
                continue
            own = random.Random(f"{args.seed}-{n}")
            name = f"qwen_{hashlib.sha1(f'{args.seed}-{n}'.encode()).hexdigest()[:10]}"
            _write_example(label, name, item, price, own.choice(CITIES), messages, new_account=cheap, rng=own)
            written[label] += 1
            print(f"  {done:3d}/{args.anzahl}  {label:7s}  {scenario[:60]}", flush=True)
    print(f"{sum(written.values())} Qwen-Chats ({written['betrug']} Betrug, {written['serioes']} seriös) → "
          f"{LISTING_DIR}. Nur Training. Weiter: scamguard retrain")
    return 0


# Fotos: Motiv + Stil. Fake-Inserate nutzen oft makellose Katalog-/Stockbilder, echte Verkäufer Handyfotos.
PHOTO_ITEMS = ["an iPhone 14 Pro", "a PlayStation 5 console with controller", "a MacBook Air laptop",
               "a Nintendo Switch OLED", "a Thermomix kitchen machine", "an electric mountain bike",
               "a Dyson cordless vacuum cleaner", "a modern grey baby stroller", "a graphics card",
               "a grey three-seater sofa", "a golden retriever puppy", "a designer handbag",
               "a pair of new sneakers", "a modern apartment living room"]
STYLE = {
    "betrug": [("professional studio product photo of {item}, pure white background, perfect lighting, "
               "catalog image, ultra sharp, no shadows"),
               "glossy advertising render of {item}, floating, dramatic studio light, 8k, flawless",
               "stock photo of {item}, brand new, perfect composition, commercial photography"],
    "serioes": [("amateur smartphone photo of a used {item} on a kitchen table at home, natural window light, "
                "slightly cluttered background"),
                ("casual phone snapshot of {item} on a wooden floor in a living room, slightly blurry, "
                "visible signs of use"),
                "photo of a second-hand {item} taken at home for an online classified ad, imperfect framing"],
}


def cmd_fotos(args) -> int:
    try:
        import torch
        from diffusers import AutoPipelineForText2Image
    except ImportError:
        sys.exit("Braucht torch + diffusers mit GPU – mit .venv-lora ausführen (pip install diffusers).")
    if not torch.cuda.is_available():
        sys.exit("Keine GPU gefunden (ROCm/CUDA).")
    pipe = AutoPipelineForText2Image.from_pretrained(args.modell, torch_dtype=torch.float16, variant="fp16")
    pipe.to("cuda")
    pipe.set_progress_bar_config(disable=True)
    rng = random.Random(args.seed)
    generator = torch.Generator("cuda").manual_seed(args.seed)
    for label in ("betrug", "serioes"):
        (IMAGE_DIR / label).mkdir(parents=True, exist_ok=True)
    for n in range(args.anzahl):
        label = "betrug" if n % 2 == 0 else "serioes"
        item = rng.choice(PHOTO_ITEMS)
        prompt = rng.choice(STYLE[label]).format(item=item)
        image = pipe(prompt=prompt, num_inference_steps=args.schritte, guidance_scale=0.0,
                     width=512, height=512, generator=generator).images[0]
        name = f"ki_{hashlib.sha1(f'{args.seed}-{n}'.encode()).hexdigest()[:10]}.png"
        image.save(IMAGE_DIR / label / name)
        print(f"  {n + 1:3d}/{args.anzahl}  {label:7s}  {item}")
    print(f"{args.anzahl} Fotos → {IMAGE_DIR}. Nur Training (Quelle „synthetisch“). Weiter: scamguard retrain --bilder")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Synthetische Trainingsdaten (nur Training, nie Testset)")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("chats", help="nachgebaute Chat-Screenshots mit Inseratstext")
    p.add_argument("--anzahl", type=int, default=200)
    p.add_argument("--anteil-betrug", type=float, default=0.5)
    p.add_argument("--seed", type=int, default=42)
    p.set_defaults(func=cmd_chats)
    p = sub.add_parser("qwen-chats", help="Chat-Screenshots mit von Qwen geschriebenen Gesprächen (Qwen-Server)")
    p.add_argument("--anzahl", type=int, default=100)
    p.add_argument("--anteil-betrug", type=float, default=0.5)
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--parallel", type=int, default=4, help="gleichzeitige Anfragen (Slots des Qwen-Servers)")
    p.set_defaults(func=cmd_qwen_chats)
    p = sub.add_parser("fotos", help="KI-Produktfotos (Stable Diffusion, braucht GPU)")
    p.add_argument("--anzahl", type=int, default=60)
    p.add_argument("--modell", default="stabilityai/sdxl-turbo")
    p.add_argument("--schritte", type=int, default=4)
    p.add_argument("--seed", type=int, default=42)
    p.set_defaults(func=cmd_fotos)
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
