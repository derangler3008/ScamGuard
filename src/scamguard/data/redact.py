"""Personenbezogene Kontaktdaten aus gesammelten Texten entfernen (Datensparsamkeit, DSGVO Art. 89).

Foren-Beiträge enthalten Mailadressen, Telefonnummern und IBANs von Betrügern, Geldkurieren oder
Opfern. Für das Training zählt nur die Form („ausländische Nummer“, „Freemail-Adresse“, „litauische
IBAN“), nicht die echte Nummer. Deshalb bleibt erhalten, was die Merkmale in features/contact.py
brauchen, und alles Identifizierende wird ersetzt:

  max.muster1987@gmail.com      → anonym@gmail.com
  +44 7911 123456               → +44 1111 111111
  0151 23456789                 → 0111 11111111 (Vorwahl-Anfang bleibt: Handy/Festnetz)
  LT12 1000 0111 0100 1000      → LT.. 1111 1111 1111 1111 (Prüfziffer neu berechnet, damit gültig)

Ersetzt wird durch „1“, nicht „0“: „0000 …“ sähe für die Telefon-Erkennung wie eine Auslandsvorwahl
(„00…“) aus.
  https://sicher-zahlen.top/p/8f3a?id=42 → https://sicher-zahlen.top/…
  @nutzername                   → @nutzer

Namen im Fließtext lassen sich so nicht zuverlässig erkennen – darum werden gesammelte Rohtexte nie
veröffentlicht (data/ ist nicht im Git).
"""

from __future__ import annotations

import re

from scamguard.features.contact import DE_PHONE_RE, EMAIL_RE, INTL_PHONE_RE, URL_RE, find_ibans

# Verschleierte Adressen aus Foren: „name [at] gmail.com“, „name(at)web(dot)de“
OBFUSCATED_EMAIL_RE = re.compile(
    r"\b[\w.+-]+\s*(?:\[at\]|\(at\)|\{at\})\s*([\w-]+(?:\s*(?:\.|\[dot\]|\(dot\))\s*[\w-]+)+)", re.IGNORECASE)
HANDLE_RE = re.compile(r"(?<![\w@./])@[A-Za-z0-9_]{3,30}\b")


def _mask_digits(text: str, keep: int = 0) -> str:
    """Ziffern durch 1 ersetzen, die ersten `keep` Zeichen bleiben (Landesvorwahl, Vorwahl-Anfang)."""
    return text[:keep] + re.sub(r"\d", "1", text[keep:])


def _iban(raw: str) -> str:
    """Land behalten, Kontonummer durch Einsen ersetzen, Prüfziffer neu berechnen – so erkennt
    features/contact.py die IBAN weiterhin (auch als ausländisch), aber kein echtes Konto."""
    country = raw[:2].upper()
    bban = re.sub(r"[A-Z0-9]", "1", raw[4:], flags=re.IGNORECASE)
    digits = "".join(str(int(c, 36)) for c in bban.replace(" ", "") + country + "00")
    return f"{country}{98 - int(digits) % 97:02d}{bban}"


def _email(match: re.Match) -> str:
    return "anonym@" + match.group(0).rsplit("@", 1)[1]


def _obfuscated_email(match: re.Match) -> str:
    domain = re.sub(r"\s*(?:\[dot\]|\(dot\))\s*|\s*\.\s*", ".", match.group(1), flags=re.IGNORECASE)
    return f"anonym@{domain.lower()}"


def _url(match: re.Match) -> str:
    url = match.group(0)
    head = re.match(r"((?:https?://)?[^/?#]+)", url)
    host = head.group(1) if head else url
    return host + ("/…" if len(url) > len(host) + 1 else url[len(host):])


def _intl_phone(match: re.Match) -> str:
    raw = match.group(0)
    return _mask_digits(raw, keep=raw.index(match.group(1)) + len(match.group(1)))


def redact(text: str) -> str:
    """Kontaktdaten anonymisieren; Reihenfolge wichtig (IBAN vor Telefonnummern, Mails vor URLs)."""
    for a, b in reversed(find_ibans(text)):
        text = text[:a] + _iban(text[a:b]) + text[b:]
    text = OBFUSCATED_EMAIL_RE.sub(_obfuscated_email, text)
    text = EMAIL_RE.sub(_email, text)
    text = INTL_PHONE_RE.sub(_intl_phone, text)
    text = DE_PHONE_RE.sub(lambda m: _mask_digits(m.group(0), keep=2), text)  # „0151 …“ → „0111 …“
    # URLs: nur Host behalten. Mailadressen (schon anonymisiert) nicht als URL behandeln
    text = URL_RE.sub(lambda m: m.group(0) if "@" in text[max(0, m.start() - 1):m.start()] else _url(m), text)
    return HANDLE_RE.sub("@nutzer", text)
