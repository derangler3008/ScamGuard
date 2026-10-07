import json

import pytest

from scamguard.config import load_config, resolve_path
from scamguard.data.loaders import normalize_category, parse_price
from scamguard.data.redact import redact
from scamguard.features.contact import analyze_contacts
from scamguard.features.language import analyze_language, looks_german
from scamguard.features.price import analyze_price
from scamguard.features.scam_phrases import match_phrases
from scamguard.schema import Listing

CFG = load_config()


def codes(signals):
    return {s.code for s in signals}


# --- URLs & E-Mails ---------------------------------------------------------------

def test_lookalike_domain_with_suspicious_tld_is_hard_signal():
    f = analyze_contacts("Geld empfangen: https://kleinanzeigen.sicher-bezahlen-de.top/zahlung/1")
    assert {"LOOKALIKE_DOMAIN", "SUSPICIOUS_TLD"} <= codes(f.signals)
    assert any(s.hard for s in f.signals)


def test_official_link_is_not_flagged():
    f = analyze_contacts("Siehe https://www.kleinanzeigen.de/s-anzeige/123 und paypal.com")
    assert not codes(f.signals) & {"LOOKALIKE_DOMAIN", "EXTERNAL_LINK"}


def test_homoglyph_domain_detected():
    f = analyze_contacts("Bestätigen auf paypa1-kaeuferschutz.com")
    assert "LOOKALIKE_DOMAIN" in codes(f.signals)


def test_short_brand_needs_own_token():
    # "ups" steckt in "groups", darf aber nicht als UPS-Imitation zählen
    f = analyze_contacts("Mehr auf meetup-groups.com")
    assert "LOOKALIKE_DOMAIN" not in codes(f.signals)


def test_impersonation_email():
    f = analyze_contacts("Schreib an kleinanzeigen.support@example.com")
    assert "IMPERSONATION_EMAIL" in codes(f.signals)
    assert f.urls == []  # Mail-Domain darf nicht zusätzlich als URL zählen


# --- IBAN & Telefon ----------------------------------------------------------------

def test_foreign_iban_is_not_mistaken_for_phone_number():
    f = analyze_contacts("Konto: GB82 WEST 1234 5698 7654 32")
    assert "FOREIGN_IBAN" in codes(f.signals)
    assert "FOREIGN_PHONE" not in codes(f.signals)


@pytest.mark.parametrize("text", ["Bordnetzsteuergerät 8X0907063S", "Halter Schließteil 8U0807233A",
                                  "Steuergerät 5Q0907530 passt für Golf 7"])
def test_part_numbers_are_not_phone_numbers(text):
    assert "PHONE_IN_TEXT" not in codes(analyze_contacts(text).signals)
    assert redact(text) == text


def test_german_iban_digits_are_not_a_foreign_phone():
    f = analyze_contacts("Konto: DE89 3704 0044 0532 0130 00")
    assert "IBAN_IN_TEXT" in codes(f.signals)
    assert not codes(f.signals) & {"FOREIGN_PHONE", "PHONE_IN_TEXT"}


def test_invalid_iban_checksum_ignored():
    assert analyze_contacts("DE00 1234 5678 9012 3456 78").ibans == []


def test_phone_country_codes():
    assert "FOREIGN_PHONE" in codes(analyze_contacts("WhatsApp +44 7700 900123").signals)
    assert "FOREIGN_PHONE" not in codes(analyze_contacts("Ruf an: +49 151 2345678").signals)


# --- Sprache --------------------------------------------------------------------

def test_wrong_article_detected():
    f = analyze_language("Der Auto ist gut und die Handy auch.")
    assert len(f.article_errors) == 2


def test_correct_plural_and_dative_not_flagged():
    f = analyze_language("Die Artikel sind neu. Mit der Waschmaschine gab es nie Probleme. Die Handy-Hülle ist dabei.")
    assert f.article_errors == []


def test_looks_german():
    assert looks_german("Ich verkaufe mein Fahrrad, es ist noch sehr gut und hat keine Mängel.")
    assert not looks_german("This is a great item and you can pay with the payment link please.")


def test_language_filter_keeps_all_german_samples():
    # Regression: kurze, sachliche Inserate ohne typische Stoppwörter wurden verworfen
    path = resolve_path("data/samples/sample_listings.jsonl")
    for line in path.read_text(encoding="utf-8").splitlines():
        listing = Listing.from_dict(json.loads(line))
        assert looks_german(listing.full_text), listing.title


# --- Lexikon & Preis --------------------------------------------------------------

def test_phrases_match_across_line_breaks():
    lexicon = str(resolve_path(CFG["paths"]["lexicon"]))
    signals = match_phrases("Zahlung per PayPal Freunde und\nFamilie.\nZahlung bereits\nveranlasst, "
                            "bitte\nklicken Sie auf den Link", lexicon)
    assert {"OFFPLATFORM_PAYMENT", "FAKE_PAYMENT_LINK"} <= codes(signals)
    # Die Markierung enthält den Originaltext inkl. Umbruch – die Extension sucht whitespace-tolerant
    assert next(s for s in signals if s.code == "OFFPLATFORM_PAYMENT").highlights == ["Freunde und\nFamilie"]


def test_whitespace_tolerant_pattern_rewrite():
    from scamguard.features.scam_phrases import whitespace_tolerant

    assert whitespace_tolerant("western ?union") == r"western\s*union"
    assert whitespace_tolerant("freunde (und|&) familie") == r"freunde\s+(und|&)\s+familie"
    assert whitespace_tolerant("dhl[- ]?treuhand") == r"dhl[-\s]?treuhand"
    assert whitespace_tolerant(r"verfügbar\?\s*$") == r"verfügbar\?\s*$"


def test_phrase_groups_match_once_per_group():
    signals = match_phrases("Zahlung per PayPal Freunde und Familie oder Western Union",
                            str(resolve_path(CFG["paths"]["lexicon"])))
    assert codes(signals) == {"OFFPLATFORM_PAYMENT"}


def _price_codes(title: str, price: float) -> set[str]:
    _, signals = analyze_price(Listing(title=title, price=price),
                               str(resolve_path(CFG["paths"]["price_reference"])))
    return codes(signals)


def test_price_levels_relative_to_lower_market_bound():
    # Referenz iPhone 14/15 Pro: ab 450 € → <40 % stark, <65 % leicht auffällig
    assert "PRICE_FAR_TOO_LOW" in _price_codes("iPhone 15 Pro 256GB", 120)
    assert "PRICE_LOW" in _price_codes("iPhone 15 Pro 256GB", 220)
    assert not _price_codes("iPhone 15 Pro 256GB", 600)
    assert not _price_codes("Unbekannter Artikel", 1)  # ohne Referenz kein Urteil


# --- Datenimport ------------------------------------------------------------------

def test_parse_price():
    assert parse_price("1.200,50 €") == 1200.5
    assert parse_price("350 VB") == 350
    assert parse_price("zu verschenken") == 0
    assert parse_price("VB") is None
    assert parse_price(float("nan")) is None


def test_normalize_category():
    assert normalize_category("Haushaltsgeräte") == "haushaltsgeraete"
    assert normalize_category("Handy & Telefon") == "elektronik"
    assert normalize_category(None) == "sonstiges"


def test_normalize_category_breadcrumb_uses_most_specific_segment():
    # So liefert die Extension die Kategorie (Brotkrumenpfad von Kleinanzeigen)
    assert normalize_category("Kleinanzeigen Mannheim > Elektronik > Haushaltsgeräte") == "haushaltsgeraete"
    assert normalize_category("Kleinanzeigen Mannheim > Elektronik > TV & Video") == "elektronik"
    assert normalize_category("Kleinanzeigen Mannheim > Auto, Rad & Boot > Autos") == "auto"
    assert normalize_category("Kleinanzeigen Mannheim > Auto, Rad & Boot > Fahrräder & Zubehör") == "sonstiges"
    assert normalize_category("Kleinanzeigen Mannheim > Haus & Garten > Gartenzubehör") == "sonstiges"


def test_highlights_contain_exact_page_text():
    # Anzeige darf gekürzt sein, die Markierung braucht den Originaltext
    f = analyze_contacts("Konto: GB82 WEST 1234 5698 7654 32, Mail an info@example.com")
    iban = next(s for s in f.signals if s.code == "FOREIGN_IBAN")
    assert iban.evidence.endswith("…")
    assert iban.highlights == ["GB82 WEST 1234 5698 7654 32"]
    assert next(s for s in f.signals if s.code == "EMAIL_IN_TEXT").highlights == ["info@example.com"]
    lang = analyze_language("Der Auto ist gut und die Handy auch.")
    assert next(s for s in lang.signals if s.code == "ARTICLE_ERRORS").highlights == ["Der Auto", "die Handy"]


def test_terse_german_listings_are_kept_but_english_is_dropped():
    from scamguard.features.language import looks_german

    for text in ("Waschmaschine\nAbholung in Mannheim, bar", "iPhone 13 128GB\nTop Zustand, Akku 89 %",
                 "Kinderwagen Bugaboo\nGut erhalten, Regenschutz dabei"):
        assert looks_german(text), text
    assert not looks_german("Brand new iPhone, shipping is available, please pay with the link")
    assert looks_german("Name, Ware via Willhaben bezahlt mob-willhaben.at/123456 – bitte bestätigen")
