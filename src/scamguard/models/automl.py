"""Welle 2: AutoML auf der Merkmalstabelle – sucht automatisch das beste klassische Verfahren.

Kandidaten: LightGBM, XGBoost, CatBoost, Random Forest, Extra Trees und logistische Regression (Suche nach
Verfahren und Einstellungen mit FLAML) sowie ein Deep Forest (`models/deep_forest.py`). Bewertet wird per
Kreuzvalidierung (ROC-AUC) auf dem Trainingssplit; das beste Verfahren wird auf allen Trainingsdaten
angepasst. Gerechnet wird auf allen CPU-Kernen.

Erklärung je Inserat: SHAP-Werte (Beitrag jedes Merkmals zum Score) – bei Baumverfahren exakt
(TreeExplainer), bei der logistischen Regression linear; beim Deep Forest gibt es keine.

Eigener Prozess: LightGBM/XGBoost und PyTorch bringen je eine eigene OpenMP-Bibliothek mit. Im selben
Prozess blockieren sie sich (Hänger) oder stürzen ab (Speicherzugriffsfehler, macOS). Training und
Vorhersage laufen deshalb in einem Hilfsprozess ohne PyTorch; Server und Web-App schicken nur das Inserat
hin und bekommen Score und Erklärung zurück.

Braucht `pip install -e ".[automl]"`, auf dem Mac zusätzlich `brew install libomp` (für LightGBM).
"""

from __future__ import annotations

import json
import multiprocessing
import threading
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from datetime import datetime
from importlib.util import find_spec
from pathlib import Path

import numpy as np

from scamguard.config import resolve_path
from scamguard.features.tabular import FeatureBuilder, base_row, feature_label, format_value
from scamguard.models.base import Detector
from scamguard.schema import Listing, ModelResult, Signal

ESTIMATORS = ["lgbm", "xgboost", "catboost", "rf", "extra_tree", "lrl1"]
ESTIMATOR_NAMES = {"lgbm": "LightGBM", "xgboost": "XGBoost", "catboost": "CatBoost", "rf": "Random Forest",
                   "extra_tree": "Extra Trees", "lrl1": "Logistische Regression", "deep_forest": "Deep Forest"}
TREE_KINDS = {"lgbm", "xgboost", "catboost", "rf", "extra_tree"}
MIN_PER_CLASS_TRAIN = 5    # darunter ist keine Kreuzvalidierung möglich
MIN_PER_CLASS_JUDGE = 20   # darunter wird trainiert (Bestenliste), aber im Scan nicht geurteilt
TOP_REASONS = 3
PREDICT_TIMEOUT_S = 120    # erster Aufruf lädt Modell und SHAP


def model_path(cfg: dict) -> Path:
    return resolve_path(cfg.get("automl", {}).get("path", "models/automl.joblib"))


def meta_path(cfg: dict) -> Path:
    """Bestenliste & Co. als JSON – lesbar, ohne die Baumbibliotheken in den Hauptprozess zu laden."""
    return model_path(cfg).with_suffix(".json")


def read_meta(cfg: dict) -> dict | None:
    path = meta_path(cfg)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def class_counts(listings: list[Listing]) -> dict[str, int]:
    c = Counter(listing.label for listing in listings)
    return {"betrug": c.get(1, 0), "serioes": c.get(0, 0)}


def sample_weights(listings: list[Listing], cfg: dict) -> np.ndarray:
    """Klassen ausgleichen (sonst lernt das Modell nur „fast alles ist Betrug“) und je Herkunft
    gewichten (`automl.source_weights`, z. B. für die Qwen-Urteile aus Welle 1)."""
    y = np.array([listing.label for listing in listings])
    counts = np.bincount(y, minlength=2)
    w = len(y) / (2 * np.maximum(counts[y], 1))
    by_source = cfg.get("automl", {}).get("source_weights") or {}
    return w * np.array([float(by_source.get(listing.source, 1.0)) for listing in listings])


def leaderboard_lines(meta: dict) -> list[str]:
    lines = []
    for kind, auc in sorted(meta["leaderboard"].items(), key=lambda kv: -kv[1]):
        mark = "  <- gewählt" if kind == meta["kind"] else ""
        lines.append(f"  {ESTIMATOR_NAMES.get(kind, kind):24s} CV-AUC {auc:.3f}{mark}")
    return lines


# --------------------------------------------------------------------------- Hilfsprozess

_POOL: ProcessPoolExecutor | None = None
_POOL_LOCK = threading.Lock()


def _spawn_pool() -> ProcessPoolExecutor:
    # „spawn“ statt „fork“: ein frischer Interpreter ohne bereits geladenes PyTorch
    return ProcessPoolExecutor(max_workers=1, mp_context=multiprocessing.get_context("spawn"))


def _run(fn, *args, timeout: float | None = None):
    """Führt `fn` im (dauerhaften) Hilfsprozess aus; ein abgestürzter Prozess wird neu gestartet."""
    global _POOL
    with _POOL_LOCK:
        if _POOL is None:
            _POOL = _spawn_pool()
        pool = _POOL
    try:
        return pool.submit(fn, *args).result(timeout=timeout)
    except BrokenProcessPool:
        with _POOL_LOCK:
            if _POOL is pool:
                _POOL = None
        raise


def train_automl(train: list[Listing], cfg: dict, time_budget: float | None = None, log=print) -> Path:
    """Startet die AutoML-Suche in einem eigenen Prozess und wartet auf das Ergebnis."""
    settings = cfg.get("automl", {})
    counts = class_counts(train)
    if min(counts.values()) < MIN_PER_CLASS_TRAIN:
        raise ValueError(f"Zu wenige Beispiele ({counts['betrug']} Betrug, {counts['serioes']} seriös; "
                         f"nötig: je {MIN_PER_CLASS_TRAIN}) – erst mehr Inserate einstufen.")
    for package in ("flaml", "lightgbm", "xgboost", "catboost", "shap"):
        if find_spec(package) is None:
            raise ImportError(f"Paket {package} fehlt", name=package)
    budget = float(time_budget or settings.get("time_budget_s", 300))
    log(f"AutoML: {len(train)} Beispiele, Suche {budget:.0f} s auf allen CPU-Kernen (eigener Prozess) …")
    with _spawn_pool() as pool:  # Training nicht im dauerhaften Vorhersage-Prozess (Speicher)
        return pool.submit(_train, train, cfg, budget).result()


def _train(train: list[Listing], cfg: dict, budget: float) -> Path:
    import joblib
    from flaml import AutoML
    from sklearn.model_selection import StratifiedKFold, cross_val_score

    from scamguard.models.deep_forest import CascadeForest

    settings = cfg.get("automl", {})
    counts = class_counts(train)
    builder = FeatureBuilder(int(settings.get("text_components", 20)))
    x = builder.fit(train, cfg)
    y = np.array([listing.label for listing in train])
    w = sample_weights(train, cfg)
    n_splits = max(2, min(5, min(counts.values())))

    automl = AutoML()
    automl.fit(x, y, task="classification", metric="roc_auc", time_budget=budget,
               estimator_list=ESTIMATORS, eval_method="cv", n_splits=n_splits, sample_weight=w,
               n_jobs=-1, seed=42, verbose=0, log_file_name="")
    leaderboard = {kind: 1.0 - loss for kind, loss in automl.best_loss_per_estimator.items()
                   if loss is not None and np.isfinite(loss)}

    forest = CascadeForest(n_estimators=int(settings.get("deep_forest_trees", 200)), n_jobs=-1)
    folds = StratifiedKFold(n_splits, shuffle=True, random_state=42)
    leaderboard["deep_forest"] = float(np.mean(cross_val_score(
        forest, x.to_numpy(), y, cv=folds, scoring="roc_auc", params={"sample_weight": w})))

    best = max(leaderboard, key=leaderboard.get)
    model = forest.fit(x.to_numpy(), y, sample_weight=w) if best == "deep_forest" else automl.model.estimator
    meta = {"kind": best, "leaderboard": leaderboard, "counts": counts, "n_features": x.shape[1],
            "n_train": len(train), "cv_folds": n_splits, "time_budget_s": budget,
            "trained_at": datetime.now().astimezone().isoformat(timespec="seconds")}
    path = model_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump({"model": model, "builder": builder, "meta": meta,
                 "background": x.sample(min(len(x), 100), random_state=42)}, path)
    meta_path(cfg).write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def _positive_class(values) -> np.ndarray:
    """SHAP liefert je nach Verfahren (n, Merkmale), (n, Merkmale, 2) oder eine Liste je Klasse."""
    if isinstance(values, list):
        values = values[-1]
    values = np.asarray(values)
    return values[..., 1] if values.ndim == 3 else values


class _Scorer:
    """Lebt im Hilfsprozess: lädt das Modell (neu, wenn die Datei neu ist) und bewertet Inserate."""

    def __init__(self, path: Path):
        self.path = path
        self.bundle: dict | None = None
        self.explainer = None
        self.mtime: float | None = None

    def predict(self, listing: Listing, cfg: dict) -> ModelResult:
        import joblib

        mtime = self.path.stat().st_mtime
        if mtime != self.mtime:
            self.bundle, self.explainer, self.mtime = joblib.load(self.path), None, mtime
        meta = self.bundle["meta"]
        counts = meta["counts"]
        if min(counts.values()) < MIN_PER_CLASS_JUDGE:
            return ModelResult(AutoMLDetector.name, score=None, error=(
                f"Trainiert mit {counts['betrug']} Betrugs- und {counts['serioes']} seriösen Beispielen – "
                f"urteilt erst ab je {MIN_PER_CLASS_JUDGE}. Mehr einstufen, dann `scamguard automl`."))
        x = self.bundle["builder"].transform([listing], cfg)
        data = x.to_numpy() if meta["kind"] == "deep_forest" else x
        prob = float(self.bundle["model"].predict_proba(data)[0][1])
        return ModelResult(AutoMLDetector.name, score=prob, signals=self._explain(x, listing, cfg))

    def _explain(self, x, listing: Listing, cfg: dict) -> list[Signal]:
        kind = self.bundle["meta"]["kind"]
        if kind not in TREE_KINDS | {"lrl1"}:
            return []
        try:
            import shap

            if self.explainer is None:
                model = self.bundle["model"]
                self.explainer = (shap.TreeExplainer(model) if kind in TREE_KINDS
                                  else shap.LinearExplainer(model, self.bundle["background"]))
            values = _positive_class(self.explainer.shap_values(x))[0]
        except Exception:  # noqa: BLE001 – Erklärung ist Zusatz, der Score zählt auch ohne
            return []
        messages = base_row(listing, cfg)[1]
        method = ESTIMATOR_NAMES.get(kind, kind)
        signals = []
        for idx in np.argsort(-np.abs(values))[:TOP_REASONS]:
            if abs(values[idx]) < 1e-6:
                break
            col = x.columns[idx]
            value = format_value(col, float(x.iloc[0, idx]))
            label = feature_label(col, messages) + (f": {value}" if value else "")
            effect = "erhöht" if values[idx] > 0 else "senkt"
            signals.append(Signal(AutoMLDetector.name, "AUTOML_REASON",
                                  f"{method}: {label} {effect} das Risiko", 0.0,
                                  evidence=f"SHAP {values[idx]:+.2f}", info=True))
        return signals


_SCORERS: dict[str, _Scorer] = {}


def _predict_in_worker(path: str, listing: Listing, cfg: dict) -> ModelResult:
    scorer = _SCORERS.setdefault(path, _Scorer(Path(path)))
    return scorer.predict(listing, cfg)


# --------------------------------------------------------------------------- Detektor (Hauptprozess)

class AutoMLDetector(Detector):
    name = "automl"

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.path = model_path(cfg)

    @property
    def available(self) -> bool:
        return self.path.exists()

    @property
    def unavailable_reason(self) -> str | None:
        return "Noch nicht trainiert → `scamguard automl`"

    def predict(self, listing: Listing) -> ModelResult:
        try:
            return _run(_predict_in_worker, str(self.path), listing, self.cfg, timeout=PREDICT_TIMEOUT_S)
        except ImportError as exc:
            return ModelResult(self.name, None, error=f"Pakete fehlen ({exc.name}) → pip install -e \".[automl]\"")
        except BrokenProcessPool:
            return ModelResult(self.name, None, error="AutoML-Prozess abgestürzt – wird beim nächsten Scan neu gestartet")
