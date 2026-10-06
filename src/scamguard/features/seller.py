"""Anbieter-Merkmale: Kontoalter, Bewertungs-Abzeichen, privat oder gewerblich, Werbung.

Kleinanzeigen zeigt bei jeder Anzeige „Privater Nutzer“ bzw. „Gewerblicher Nutzer“, „Aktiv seit …“,
Abzeichen wie „TOP Zufriedenheit“, „Sehr freundlich“, „Besonders zuverlässig“ und die Zahl der
Anzeigen; Firmen tragen oft eine Rechtsform im Namen (GmbH, UG, GbR …). Daraus entstehen

- Warnsignale: sehr junges Konto, hochpreisiger Artikel ohne jede Bewertung, schwache Zufriedenheit.
- Kontext (Signal.info): das Anbieterprofil, gewerbliche Anbieter, Dienstleistungen/Werbung. Gute
  Bewertungen senken das Risiko bewusst nicht – gehackte Konten mit guter Historie sind eine
  verbreitete Masche –, sie stehen aber im Panel und gehen an das LLM.

Bei gewerblichen Anbietern sind Telefonnummer, Mailadresse, Website und Impressum normal; die
entsprechenden Kontakt-Signale werden deshalb abgeschwächt (business_contact_adjust).
"""

from __future__ import annotations

from scamguard.schema import SELLER_BADGES, Listing, Signal

SRC = "rules"
# Kontaktwege, die bei Händlern zum Geschäft gehören (harte Signale wie Fake-Domains bleiben unberührt)
BUSINESS_CONTACT = {"PHONE_IN_TEXT", "EMAIL_IN_TEXT", "EXTERNAL_LINK", "OFFPLATFORM_CONTACT"}
BUSINESS_FACTOR = 0.3
HIGH_VALUE = 200  # €


def is_commercial(listing: Listing) -> bool:
    """Gewerblich laut Anzeige – oder Rechtsform im Namen, ohne dass das Konto als privat auftritt."""
    return listing.seller_type == "gewerblich" or (bool(listing.seller_legal_form) and listing.seller_type is None)


def _age_text(days: int) -> str:
    if days < 60:
        return f"seit {days} Tag{'' if days == 1 else 'en'} aktiv"
    years, months = divmod(days // 30, 12)
    return f"seit {years} Jahr{'' if years == 1 else 'en'} aktiv" if years else f"seit {months} Monaten aktiv"


def profile_summary(listing: Listing) -> str:
    """„Privater Nutzer · seit 6 Tagen aktiv · TOP Zufriedenheit, Zuverlässig · 7 Anzeigen“."""
    parts = []
    form = f" ({listing.seller_legal_form})" if listing.seller_legal_form else ""
    if is_commercial(listing):
        parts.append(f"Gewerblicher Anbieter{form}")
    elif listing.seller_type == "privat":
        parts.append(f"Privater Nutzer{form}")
    if listing.seller_account_age_days is not None:
        parts.append(_age_text(listing.seller_account_age_days))
    if listing.seller_badges:
        parts.append(", ".join(SELLER_BADGES[b] for b in listing.seller_badges))
    elif listing.seller_type:
        parts.append("keine Bewertungs-Abzeichen")
    if listing.seller_num_ads is not None:
        parts.append(f"{listing.seller_num_ads} Anzeige{'' if listing.seller_num_ads == 1 else 'n'}")
    return " · ".join(parts)


def analyze_seller(listing: Listing) -> tuple[dict[str, float], list[Signal]]:
    features: dict[str, float] = {}
    signals: list[Signal] = []
    age, badges = listing.seller_account_age_days, set(listing.seller_badges)
    commercial = is_commercial(listing)
    profile_known = listing.seller_type is not None  # Anbieterbox gelesen → fehlende Abzeichen sind echt

    if age is not None:
        features["seller_account_age_days"] = float(age)
        if age < 7:
            signals.append(Signal(SRC, "NEW_ACCOUNT", "Verkäuferkonto ist jünger als eine Woche", 0.3,
                                  evidence=f"{age} Tage", target="seller"))
        elif age < 30:
            signals.append(Signal(SRC, "YOUNG_ACCOUNT", "Verkäuferkonto ist jünger als ein Monat", 0.1,
                                  evidence=f"{age} Tage", target="seller"))

    rated = any(b.startswith("zufriedenheit_") for b in badges)
    features.update(seller_commercial=float(commercial), seller_rated=float(rated),
                    seller_top_rating=float("zufriedenheit_top" in badges), seller_n_badges=float(len(badges)))
    if listing.seller_num_ads is not None:
        features["seller_num_ads"] = float(listing.seller_num_ads)
    no_ratings = listing.seller_num_ratings == 0 or (profile_known and not rated)
    if (no_ratings and not commercial and listing.price is not None and listing.price >= HIGH_VALUE
            and (age is None or age < 365)):
        signals.append(Signal(SRC, "NO_RATINGS_HIGH_VALUE", "Hochpreisiger Artikel von Konto ohne Bewertungen",
                              0.15, target="seller"))
    if listing.seller_type == "privat" and listing.seller_legal_form:
        signals.append(Signal(SRC, "COMMERCIAL_AS_PRIVATE",
                              f"Firmenname ({listing.seller_legal_form}), aber als „Privater Nutzer“ eingestellt – "
                              "Händler umgehen so Gewährleistung und Widerrufsrecht", 0.15,
                              evidence=listing.seller_legal_form, target="seller"))
    if "zufriedenheit_naja" in badges:
        signals.append(Signal(SRC, "LOW_SATISFACTION", "Anbieter hat nur „NA JA“-Zufriedenheit", 0.15,
                              evidence="NA JA Zufriedenheit", target="seller"))

    if summary := profile_summary(listing):
        signals.append(Signal(SRC, "SELLER_PROFILE", f"Anbieter: {summary}", 0.0, target="seller", info=True))
    if commercial:
        signals.append(Signal(SRC, "COMMERCIAL_SELLER",
                              "Gewerblicher Anbieter (Händler/Firma): geschäftliche Telefonnummer, Adresse und "
                              "Impressum sind hier normal", 0.0, target="seller", info=True))
    if listing.category == "dienstleistungen" or (commercial and listing.price is None):
        signals.append(Signal(SRC, "SERVICE_OR_AD",
                              "Dienstleistung bzw. Werbeanzeige – Preis wird ausgehandelt, kein Privatverkauf",
                              0.0, info=True))
    return features, signals


def business_contact_adjust(listing: Listing, signals: list[Signal]) -> None:
    """Kontakt-Signale bei gewerblichen Anbietern abschwächen (in place)."""
    if not is_commercial(listing):
        return
    for s in signals:
        if s.code in BUSINESS_CONTACT and not s.hard and not s.info:
            s.weight = round(s.weight * BUSINESS_FACTOR, 3)
            s.message += " (bei gewerblichen Anbietern üblich)"
