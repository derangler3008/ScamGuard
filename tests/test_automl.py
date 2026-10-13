"""Welle 2: Merkmalstabelle, Deep Forest, AutoML im eigenen Prozess (ohne PyTorch)."""

import random
import sys

import numpy as np
import pytest

from scamguard.config import load_config, with_overrides
from scamguard.features.tabular import MISSING, FeatureBuilder, feature_label, format_value
from scamguard.models.automl import MIN_PER_CLASS_JUDGE, AutoMLDetector, _run, sample_weights
from scamguard.models.deep_forest import CascadeForest
from scamguard.schema import Listing

CFG = load_config()

SCAM = ["Zahlung nur per PayPal Freunde und Familie, Versand mit DHL.",
        "Bitte vorab überweisen, ich bin im Ausland. Schreib mir auf WhatsApp.",
        "Nur Vorkasse, Abholung nicht möglich. Kontakt über Telegram."]
LEGIT = ["Abholung in Mannheim, Barzahlung bei Abholung.", "Gerne vorher anschauen, Besichtigung jederzeit.",
         "Privatverkauf, keine Garantie, Abholung bevorzugt."]
ITEMS = ["Fahrrad", "Sofa", "iPhone 13", "Waschmaschine", "Kinderwagen", "Gitarre", "Monitor", "Tisch"]


def _listings(n: int, seed: int = 0) -> list[Listing]:
    rnd = random.Random(seed)
    out = []
    for i in range(n):
        scam = i % 2 == 0
        out.append(Listing(
            title=f"{rnd.choice(ITEMS)} {i}", description=rnd.choice(SCAM if scam else LEGIT),
            price=float(rnd.randint(20, 400)), category=rnd.choice(["elektronik", "moebel"]),
            seller_account_age_days=rnd.randint(0, 20) if scam else rnd.randint(400, 3000),
            seller_type="privat", label=int(scam), source="test"))
    return out


# --------------------------------------------------------------------------- Merkmalstabelle

def test_feature_table_has_fixed_columns_and_marks_missing_values():
    train = _listings(30)
    builder = FeatureBuilder(text_components=5)
    x = builder.fit(train, CFG)
    assert list(x.columns) == builder.columns and len(x) == 30
    assert all(x[c].nunique() > 1 for c in x.columns)  # konstante Spalten fallen weg
    assert any(c.startswith("text_komponente_") for c in x.columns)

    unseen = Listing(title="Konsole", description="Bitte über Western Union zahlen", seller_account_age_days=None)
    row = builder.transform([unseen], CFG)
    assert list(row.columns) == builder.columns  # unbekannte Warnsignale werden ignoriert
    assert row["seller_account_age_days"].iloc[0] == MISSING


def test_feature_labels_are_readable():
    assert feature_label("seller_account_age_days") == "Kontoalter (Tage)"
    assert feature_label("signal_pressure_urgency", {"signal_pressure_urgency": "Zeitdruck"}) == "Warnsignal „Zeitdruck“"
    assert feature_label("category_moebel") == "Kategorie Möbel"
    assert format_value("seller_account_age_days", MISSING) == "unbekannt"
    assert format_value("seller_private", 1.0) == "ja"
    assert format_value("text_komponente_3", 0.42) is None


def test_sample_weights_balance_classes_and_sources():
    listings = _listings(10) + [Listing(title="x", label=1, source="qwen")] * 6
    w = sample_weights(listings, with_overrides(CFG, {"automl": {"source_weights": {"qwen": 0.5}}}))
    y = np.array([listing.label for listing in listings])
    assert w[-1] == pytest.approx(w[0] * 0.5)  # gleiche Klasse, halbes Gewicht für Qwen-Urteile
    unweighted = sample_weights(listings, CFG)
    assert unweighted[y == 1].sum() == pytest.approx(unweighted[y == 0].sum())


# --------------------------------------------------------------------------- Deep Forest

def test_cascade_forest_learns_and_stops_adding_layers():
    rnd = np.random.default_rng(0)
    x = rnd.normal(size=(120, 6))
    y = (x[:, 0] + 0.5 * x[:, 1] > 0).astype(int)
    forest = CascadeForest(n_estimators=30, max_layers=4).fit(x[:80], y[:80])
    probs = forest.predict_proba(x[80:])
    assert probs.shape == (40, 2) and np.allclose(probs.sum(axis=1), 1)
    assert (forest.predict(x[80:]) == y[80:]).mean() > 0.8
    assert 1 <= len(forest.layers_) <= 4
    assert all(b > a for a, b in zip(forest.layer_scores_, forest.layer_scores_[1:]))


# --------------------------------------------------------------------------- Eigener Prozess

def _torch_loaded() -> bool:
    return "torch" in sys.modules


def _fake_bundle(tmp_path, n_per_class: int):
    """Kleines Extra-Trees-Modell ohne AutoML-Suche – testet Vorhersage und Erklärung im Hilfsprozess."""
    import joblib
    from sklearn.ensemble import ExtraTreesClassifier

    train = _listings(2 * n_per_class)
    builder = FeatureBuilder(text_components=5)
    x = builder.fit(train, CFG)
    model = ExtraTreesClassifier(n_estimators=50, random_state=0).fit(x, [l.label for l in train])
    path = tmp_path / f"automl_{n_per_class}.joblib"
    joblib.dump({"model": model, "builder": builder, "background": x,
                 "meta": {"kind": "extra_tree", "leaderboard": {"extra_tree": 1.0},
                          "counts": {"betrug": n_per_class, "serioes": n_per_class}}}, path)
    return with_overrides(CFG, {"automl": {"path": str(path)}})


def test_automl_predicts_in_separate_process_with_shap_reasons(tmp_path):
    pytest.importorskip("shap")
    det = AutoMLDetector(_fake_bundle(tmp_path, MIN_PER_CLASS_JUDGE + 5))
    assert det.available
    scam = det.predict(Listing(title="Fahrrad", description=SCAM[0], seller_account_age_days=2))
    legit = det.predict(Listing(title="Fahrrad", description=LEGIT[0], seller_account_age_days=2000))
    assert scam.score > 0.5 > legit.score
    assert scam.signals and all(s.info and s.source == "automl" for s in scam.signals)
    assert scam.signals[0].message.startswith("Extra Trees: ")
    assert _run(_torch_loaded) is False  # Baumbibliotheken und PyTorch nie im selben Prozess


def test_automl_abstains_with_too_few_examples(tmp_path):
    det = AutoMLDetector(_fake_bundle(tmp_path, 8))
    result = det.predict(Listing(title="Sofa", description=LEGIT[1]))
    assert result.score is None and f"ab je {MIN_PER_CLASS_JUDGE}" in result.error


def test_untrained_automl_is_unavailable(tmp_path):
    det = AutoMLDetector(with_overrides(CFG, {"automl": {"path": str(tmp_path / "fehlt.joblib")}}))
    assert not det.available and "scamguard automl" in det.unavailable_reason


def test_train_automl_end_to_end(tmp_path):
    pytest.importorskip("flaml")
    from scamguard.models.automl import read_meta, train_automl

    cfg = with_overrides(CFG, {"automl": {"path": str(tmp_path / "automl.joblib"), "text_components": 5,
                                          "deep_forest_trees": 20}})
    path = train_automl(_listings(2 * MIN_PER_CLASS_JUDGE + 4), cfg, time_budget=4, log=lambda _: None)
    meta = read_meta(cfg)
    assert path.exists() and meta["kind"] in meta["leaderboard"] and "deep_forest" in meta["leaderboard"]
    assert meta["counts"] == {"betrug": MIN_PER_CLASS_JUDGE + 2, "serioes": MIN_PER_CLASS_JUDGE + 2}
    result = AutoMLDetector(cfg).predict(Listing(title="Monitor", description=SCAM[1], seller_account_age_days=1))
    assert result.score is not None and result.score > 0.5


def test_train_automl_refuses_one_sided_data(tmp_path):
    from scamguard.models.automl import train_automl

    one_sided = [l for l in _listings(40) if l.label == 1] + _listings(6)[1::2]
    with pytest.raises(ValueError, match="Zu wenige"):
        train_automl(one_sided, CFG)
