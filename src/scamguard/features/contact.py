"""URLs, E-Mail-Adressen, Telefonnummern und IBANs finden und bewerten.

Arbeitet komplett offline (keine DNS- oder WHOIS-Abfragen), damit Scans
reproduzierbar sind und keine Daten das Gerät verlassen.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from scamguard.schema import Signal

try:  # tldextract kennt alle Public Suffixes (z. B. .co.uk) – offline mit mitgeliefertem Snapshot
    import tldextract

    _TLD = tldextract.TLDExtract(suffix_list_urls=())
except ImportError:  # pragma: no cover
    _TLD = None

# --- Referenzlisten (bei Bedarf erweitern) ---------------------------------

OFFICIAL_DOMAINS = {
    "kleinanzeigen.de", "ebay-kleinanzeigen.de", "ebay.de", "ebay.com", "paypal.com", "paypal.de", "paypal.me",
    "dhl.de", "dhl.com", "myhermes.de", "hermesworld.com", "hermes.com", "dpd.de", "dpd.com",
    "gls-pakete.de", "gls-group.eu", "ups.com", "mobile.de", "autoscout24.de",
    "klarna.com", "amazon.de", "amazon.com", "sparkasse.de", "commerzbank.de", "deutsche-bank.de",
}
# Marken, die in Phishing-Domains gern imitiert werden
BRANDS = [
    "kleinanzeigen", "ebay", "paypal", "dhl", "hermes", "dpd", "gls", "ups",
    "autoscout", "klarna", "amazon",
]
URL_SHORTENERS = {
    "bit.ly", "tinyurl.com", "t.co", "goo.gl", "is.gd", "cutt.ly", "rebrand.ly",
    "shorturl.at", "t.ly", "ow.ly", "rb.gy", "s.id", "tiny.cc", "v.gd",
}
SUSPICIOUS_TLDS = {
    "xyz", "top", "icu", "online", "site", "shop", "live", "click", "link", "tk", "ml",
    "ga", "cf", "gq", "pw", "cc", "buzz", "rest", "fit", "support", "help", "services",
    "cyou", "sbs", "cfd", "monster", "quest",
}
DACH_COUNTRY_CODES = ("49", "43", "41")
DACH_IBAN_COUNTRIES = {"DE", "AT", "CH", "LI", "LU"}

# Ziffern/Zeichen, die Betrüger als Buchstaben-Ersatz nutzen (paypa1, k1einanzeigen)
_HOMOGLYPHS = str.maketrans({"0": "o", "1": "l", "3": "e", "4": "a", "5": "s", "7": "t", "@": "a"})

# --- Reguläre Ausdrücke ------------------------------------------------------

EMAIL_RE = re.compile(r"\b[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,24}\b", re.IGNORECASE)
_TLD_ALT = (
    "de|com|net|org|info|eu|at|ch|io|co|me|biz|app|store|"
    + "|".join(sorted(SUSPICIOUS_TLDS))
)
URL_RE = re.compile(
    r"(?:https?://|www\.)[^\s<>\"')\]]+"
    rf"|\b[a-z0-9][a-z0-9-]{{0,62}}(?:\.[a-z0-9-]{{1,63}})*\.(?:{_TLD_ALT})\b(?:/[^\s<>\"')\]]*)?",
    re.IGNORECASE,
)
INTL_PHONE_RE = re.compile(r"(?:\+|\b00)(\d{1,3})[\s\-/.]?\(?\d{1,5}\)?(?:[\s\-/.]?\d{2,}){1,4}")
DE_PHONE_RE = re.compile(r"(?<![\d+])0\d{2,5}[\s\-/.]?\d{3,}(?:[\s\-/.]?\d{2,}){0,3}")
IBAN_RE = re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){3,7}(?: ?[A-Z0-9]{1,4})?\b", re.IGNORECASE)


@dataclass
class ContactFeatures:
    urls: list[str] = field(default_factory=list)
    emails: list[str] = field(default_factory=list)
    phones: list[str] = field(default_factory=list)
    ibans: list[str] = field(default_factory=list)
    signals: list[Signal] = field(default_factory=list)

    def as_vector(self) -> dict[str, float]:
        """Numerische Features (z. B. für ein tabellarisches Modell)."""
        codes = {s.code for s in self.signals}
        return {
            "n_urls": len(self.urls),
            "n_emails": len(self.emails),
            "n_phones": len(self.phones),
            "n_ibans": len(self.ibans),
            "has_lookalike_domain": float("LOOKALIKE_DOMAIN" in codes),
            "has_shortener": float("URL_SHORTENER" in codes),
            "has_foreign_phone": float("FOREIGN_PHONE" in codes),
            "has_foreign_iban": float("FOREIGN_IBAN" in codes),
        }


# --- Hilfsfunktionen ---------------------------------------------------------

def _levenshtein(a: str, b: str) -> int:
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def split_host(host: str) -> tuple[str, str, str]:
    """→ (subdomain, domain, suffix), z. B. 'pay.paypal-secure.xyz' → ('pay', 'paypal-secure', 'xyz')."""
    host = host.lower().strip(".")
    if _TLD is not None:
        ext = _TLD(host)
        return ext.subdomain, ext.domain, ext.suffix
    parts = host.split(".")
    if len(parts) < 2:
        return "", host, ""
    return ".".join(parts[:-2]), parts[-2], parts[-1]


def _host_of(url: str) -> str:
    url = re.sub(r"^https?://", "", url.strip(), flags=re.IGNORECASE)
    host = re.split(r"[/?#]", url, maxsplit=1)[0]
    host = host.split("@")[-1]          # user:pass@host
    return host.split(":")[0].lower()   # Port entfernen


def _imitated_brand(host: str) -> str | None:
    """Gibt die imitierte Marke zurück, falls der Host eine Marke nachahmt, aber nicht offiziell ist."""
    _sub, domain, suffix = split_host(host)
    registered = f"{domain}.{suffix}" if suffix else domain
    if registered in OFFICIAL_DOMAINS:
        return None
    normalized = host.translate(_HOMOGLYPHS)
    tokens = set(re.split(r"[.\-]", normalized))
    for brand in BRANDS:
        # Kurze Marken (dhl, ups) nur als eigenes Token, sonst zu viele Fehltreffer
        if (len(brand) >= 5 and brand in normalized) or brand in tokens:
            return brand
        if len(brand) >= 5 and 0 < _levenshtein(domain.translate(_HOMOGLYPHS), brand) <= 2:
            return brand
    return None


def _iban_is_valid(iban: str) -> bool:
    s = iban.replace(" ", "").upper()
    if not 15 <= len(s) <= 34:
        return False
    rearranged = s[4:] + s[:4]
    digits = "".join(str(int(c, 36)) for c in rearranged)
    return int(digits) % 97 == 1


# --- Hauptfunktion ------------------------------------------------------------

def analyze_contacts(text: str) -> ContactFeatures:
    feats = ContactFeatures()
    src = "rules"

    # E-Mails zuerst entfernen, sonst wird der Domain-Teil als URL erkannt
    feats.emails = sorted(set(EMAIL_RE.findall(text)))
    text_wo_mail = EMAIL_RE.sub(" ", text)

    # URLs
    feats.urls = sorted({u.rstrip(".,;:!?") for u in URL_RE.findall(text_wo_mail)})
    for url in feats.urls:
        host = _host_of(url)
        _, domain, suffix = split_host(host)
        registered = f"{domain}.{suffix}" if suffix else domain

        brand = _imitated_brand(host)
        if brand:
            feats.signals.append(Signal(
                src, "LOOKALIKE_DOMAIN",
                f"Link imitiert „{brand}“, gehört aber nicht zur offiziellen Domain",
                0.9, evidence=url, hard=True,
            ))
        if registered in URL_SHORTENERS:
            feats.signals.append(Signal(src, "URL_SHORTENER", "Gekürzter Link verschleiert das Ziel",
                                        0.45, evidence=url))
        if suffix.split(".")[-1] in SUSPICIOUS_TLDS:
            feats.signals.append(Signal(src, "SUSPICIOUS_TLD",
                                        f"Ungewöhnliche Top-Level-Domain „.{suffix}“", 0.4, evidence=url))
        if re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}", host):
            feats.signals.append(Signal(src, "IP_URL", "Link zeigt direkt auf eine IP-Adresse",
                                        0.6, evidence=url))
        if "xn--" in host:
            feats.signals.append(Signal(src, "PUNYCODE_URL",
                                        "Link nutzt Punycode (mögliche Zeichen-Täuschung)", 0.6, evidence=url))
        if registered not in OFFICIAL_DOMAINS and not brand:
            feats.signals.append(Signal(src, "EXTERNAL_LINK",
                                        "Externer Link im Inserat/Chat", 0.2, evidence=url))

    # E-Mails
    for mail in feats.emails:
        local, _, mail_host = mail.lower().partition("@")
        brand = _imitated_brand(mail_host)
        local_brand = next((b for b in BRANDS if len(b) >= 5 and b in local.translate(_HOMOGLYPHS)), None)
        if brand or local_brand:
            feats.signals.append(Signal(
                src, "IMPERSONATION_EMAIL",
                f"E-Mail gibt sich als „{brand or local_brand}“ aus", 0.8, evidence=mail, hard=True,
            ))
        else:
            feats.signals.append(Signal(src, "EMAIL_IN_TEXT",
                                        "E-Mail-Adresse im Text (Kontakt außerhalb der Plattform)",
                                        0.3, evidence=mail))

    # IBANs (nur mit gültiger Prüfsumme, um Fehltreffer zu vermeiden).
    # Vor der Telefonsuche, weil IBAN-Ziffernblöcke sonst wie Nummern aussehen.
    feats.ibans = sorted({m.upper() for m in IBAN_RE.findall(text_wo_mail) if _iban_is_valid(m)})
    text_wo_iban = IBAN_RE.sub(" ", text_wo_mail)

    # Telefonnummern
    intl = [(m.group(0), m.group(1)) for m in INTL_PHONE_RE.finditer(text_wo_iban)]
    national = DE_PHONE_RE.findall(text_wo_iban)
    feats.phones = sorted({p for p, _ in intl} | set(national))
    for number, cc in intl:
        if not cc.startswith(DACH_COUNTRY_CODES):
            feats.signals.append(Signal(src, "FOREIGN_PHONE",
                                        "Ausländische Telefonnummer (außerhalb DACH)", 0.45, evidence=number))
    if feats.phones:
        feats.signals.append(Signal(src, "PHONE_IN_TEXT",
                                    "Telefonnummer im Text (Kontakt außerhalb der Plattform)",
                                    0.15, evidence=feats.phones[0]))

    for iban in feats.ibans:
        country = iban[:2]
        if country in DACH_IBAN_COUNTRIES:
            feats.signals.append(Signal(src, "IBAN_IN_TEXT",
                                        "Bankverbindung direkt im Text (Hinweis auf Vorkasse)",
                                        0.3, evidence=iban[:8] + "…"))
        else:
            feats.signals.append(Signal(src, "FOREIGN_IBAN",
                                        f"Ausländische Bankverbindung ({country})", 0.55,
                                        evidence=iban[:8] + "…"))
    return feats
