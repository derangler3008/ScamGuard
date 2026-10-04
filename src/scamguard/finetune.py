"""Trainingsdaten für das Feintuning des lokalen LLM (Qwen) mit LoRA erzeugen.

Ablauf (Details in docs/llm_feintuning.md):
  scamguard data build        → feste Splits train/val/test (dieselben wie für die anderen Modelle)
  scamguard llm-daten         → data/finetune/{train,valid,test}.jsonl  (dieses Modul)
  mlx_lm.lora … --train       → LoRA-Adapter in models/qwen_lora/
  scamguard llm-server --adapter models/qwen_lora   bzw. llm.local.adapter_path in config.yaml

Format: Chat-Format von mlx_lm (eine Zeile = {"messages": [system, user, assistant]}). System- und
Nutzernachricht sind exakt die, die der LLM-Judge im Betrieb schickt – das Modell lernt also genau
die Aufgabe, die es später löst. Die Zielantwort (assistant) wird aus euren Daten gebaut:

  scam_probability   aus dem Label (0.9 Betrug / 0.1 seriös)
  scam_type          aus dem Feld scam_type des Datensatzes, sonst aus den Regel-Treffern abgeleitet
  red_flags          die stärksten Regel-Treffer mit wörtlichem Zitat (nur bei Betrug)
  summary            kurzer Satz aus den Treffern

Damit lernt Qwen eure Labels (wann ist etwas Betrug?) und das Antwortformat. Die Begründungen sind nur
so gut wie die Regeln (Weak Supervision) – für bessere schreibt ihr für einige Beispiele eigene.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from scamguard.config import load_config, resolve_path
from scamguard.data.build import read_split
from scamguard.features import extract_features
from scamguard.models.llm_judge import (
    JSON_INSTRUCTIONS,
    MAX_RED_FLAGS,
    SCAM_TYPES,
    SYSTEM_PROMPT,
    _format_listing,
)
from scamguard.schema import Listing, Signal

OUTPUT_DIR = "data/finetune"
SPLIT_FILES = {"train": "train", "val": "valid", "test": "test"}  # mlx_lm erwartet „valid“
PROBABILITY = {1: 0.9, 0: 0.1}
MAX_WORDS_EXPLANATION, MAX_WORDS_EVIDENCE = 15, 8

# Regel-Treffer → Masche im Antwortformat des LLM-Judge (SCAM_TYPES)
SCAM_TYPE_BY_SIGNAL = {
    "FAKE_PAYMENT_LINK": "fake_zahlungslink", "LOOKALIKE_DOMAIN": "fake_zahlungslink",
    "PAYMENT_PENDING_STORY": "fake_zahlungslink", "CODE_REQUEST": "phishing",
    "ACCOUNT_PHISHING": "phishing", "PARCEL_CUSTOMS": "phishing", "IMPERSONATION_EMAIL": "phishing",
    "SAFE_ACCOUNT_SCAM": "phishing", "NEW_NUMBER_FAMILY": "phishing",
    "IDENTITY_PHISHING": "identitaetsdiebstahl", "TRIANGLE_FRAUD": "dreiecksbetrug",
    "OVERPAYMENT_REVERSE": "ueberzahlung", "OFFPLATFORM_PAYMENT": "vorkasse",
    "PAYMENT_FIRST": "vorkasse", "SHIPPING_OR_ESCROW_STORY": "vorkasse", "SELLER_ABROAD": "vorkasse",
    "ANIMAL_TRANSPORT_FEE": "vorkasse", "RENTAL_SCAM": "vorkasse",
}
# Freie scam_type-Werte aus Datensätzen → feste Kategorien
SCAM_TYPE_ALIASES = {
    "fake_paypal": "paypal_freunde", "paypal": "paypal_freunde", "phishing_link": "fake_zahlungslink",
    "sicher_bezahlen": "fake_zahlungslink", "dreieck": "dreiecksbetrug", "identitaet": "identitaetsdiebstahl",
}
BROKEN_LANGUAGE = {"NO_NOUN_CAPITALIZATION", "UMLAUT_SUBSTITUTES", "MIXED_ENGLISH"}


def _shorten(text: str, words: int) -> str:
    parts = text.split()
    return " ".join(parts[:words]) + ("…" if len(parts) > words else "")


def _scam_type(listing: Listing, signals: list[Signal]) -> str:
    if listing.label == 0:
        return "keiner"
    given = (listing.scam_type or "").strip().lower()
    given = SCAM_TYPE_ALIASES.get(given, given)
    if given in SCAM_TYPES:
        return given
    for s in signals:  # stärkstes Signal zuerst
        if s.code == "OFFPLATFORM_PAYMENT" and "freunde" in (s.evidence or "").lower():
            return "paypal_freunde"
        if s.code in SCAM_TYPE_BY_SIGNAL:
            return SCAM_TYPE_BY_SIGNAL[s.code]
    return "fake_inserat_sonstiges"


def target_answer(listing: Listing, cfg: dict) -> dict:
    """Die Antwort, die Qwen für dieses Beispiel lernen soll (gleiches Format wie im Betrieb)."""
    signals = sorted(extract_features(listing, cfg).signals, key=lambda s: s.weight, reverse=True)
    codes = {s.code for s in signals}
    language = "gebrochen" if len(codes & BROKEN_LANGUAGE) >= 2 else (
        "leichte_fehler" if codes & BROKEN_LANGUAGE else "muttersprachlich")
    flags = []
    if listing.label == 1:
        for s in signals:
            if not s.evidence or len(flags) >= MAX_RED_FLAGS:
                continue
            flags.append({"code": s.code.lower(), "explanation": _shorten(s.message, MAX_WORDS_EXPLANATION),
                          "evidence": _shorten(s.evidence, MAX_WORDS_EVIDENCE)})
    if listing.label == 1:
        summary = ("Betrug wahrscheinlich: " + "; ".join(f["explanation"] for f in flags[:2])
                   if flags else "Betrug wahrscheinlich: passt zu bekannten Maschen, auch ohne eindeutige Schlüsselwörter.")
    else:
        summary = "Keine typischen Warnsignale – wirkt wie ein gewöhnliches privates Angebot."
    return {"scam_probability": PROBABILITY[listing.label], "scam_type": _scam_type(listing, signals),
            "language_quality": language, "red_flags": flags, "summary": _shorten(summary, 25)}


def example(listing: Listing, cfg: dict) -> dict:
    return {"messages": [
        {"role": "system", "content": f"{SYSTEM_PROMPT}\n\n{JSON_INSTRUCTIONS}"},
        {"role": "user", "content": _format_listing(listing)},
        {"role": "assistant", "content": json.dumps(target_answer(listing, cfg), ensure_ascii=False)},
    ]}


def export(output_dir: str = OUTPUT_DIR, cfg: dict | None = None) -> dict[str, Counter]:
    """data/processed/*.jsonl → data/finetune/{train,valid,test}.jsonl. Rückgabe: Labels je Split."""
    cfg = cfg or load_config()
    out = resolve_path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    stats = {}
    for split, name in SPLIT_FILES.items():
        listings = [l for l in read_split(split) if l.label in PROBABILITY and l.full_text]
        with (Path(out) / f"{name}.jsonl").open("w", encoding="utf-8") as f:
            for listing in listings:
                f.write(json.dumps(example(listing, cfg), ensure_ascii=False) + "\n")
        stats[name] = Counter("betrug" if l.label else "seriös" for l in listings)
    return stats
