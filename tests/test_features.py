import json

from scamguard.config import load_config, resolve_path
from scamguard.data.loaders import normalize_category, parse_price
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
