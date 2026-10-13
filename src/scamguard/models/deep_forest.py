"""Deep Forest (Kaskadenwald nach gcForest, Zhou & Feng 2017) für Tabellendaten.

Das Paket `deep-forest` gibt es nicht für Python 3.12 – das Prinzip passt in wenige Zeilen scikit-learn:
Jede Schicht besteht aus zwei Random Forests und zwei Extra-Trees-Wäldern. Deren Klassen-
wahrscheinlichkeiten werden an die Merkmale angehängt und sind Eingabe der nächsten Schicht. Für das
Training stammen sie aus k-facher Kreuzvalidierung (sonst lernt die nächste Schicht auf überangepassten
Werten). Neue Schichten kommen hinzu, solange die Kreuzvalidierungs-AUC steigt – die Tiefe passt sich
so den Daten an. Ohne Multi-Grained Scanning (das ist für Bilder/Sequenzen gedacht).
"""

from __future__ import annotations

import numpy as np
from sklearn.base import BaseEstimator, ClassifierMixin
from sklearn.ensemble import ExtraTreesClassifier, RandomForestClassifier
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import StratifiedKFold, cross_val_predict


class CascadeForest(ClassifierMixin, BaseEstimator):
    def __init__(self, n_estimators: int = 200, max_layers: int = 5, cv: int = 3, tol: float = 1e-3,
                 n_jobs: int = -1, random_state: int = 42):
        self.n_estimators = n_estimators
        self.max_layers = max_layers
        self.cv = cv
        self.tol = tol
        self.n_jobs = n_jobs
        self.random_state = random_state

    def _layer(self, depth: int) -> list:
        seed = self.random_state + 4 * depth
        kw = {"n_estimators": self.n_estimators, "n_jobs": self.n_jobs}
        return [RandomForestClassifier(random_state=seed, **kw),
                RandomForestClassifier(random_state=seed + 1, max_features=1, **kw),  # „vollständig zufällig“
                ExtraTreesClassifier(random_state=seed + 2, **kw),
                ExtraTreesClassifier(random_state=seed + 3, max_features=1, **kw)]

    def fit(self, X, y, sample_weight=None):
        X, y = np.asarray(X, dtype=float), np.asarray(y)
        self.classes_ = np.unique(y)
        if len(self.classes_) != 2:
            raise ValueError("Deep Forest braucht Beispiele beider Klassen")
        folds = StratifiedKFold(min(self.cv, int(np.bincount(y).min())), shuffle=True,
                                random_state=self.random_state)
        params = {"sample_weight": sample_weight} if sample_weight is not None else None
        self.layers_, self.layer_scores_ = [], []
        features = X
        for depth in range(self.max_layers):
            layer = self._layer(depth)
            oof = [cross_val_predict(est, features, y, cv=folds, method="predict_proba", params=params)
                   for est in layer]
            score = roc_auc_score(y, np.mean(oof, axis=0)[:, 1])
            if self.layers_ and score <= self.layer_scores_[-1] + self.tol:
                break  # keine Verbesserung → diese Schicht verwerfen
            for est in layer:
                est.fit(features, y, sample_weight=sample_weight)
            self.layers_.append(layer)
            self.layer_scores_.append(score)
            features = np.hstack([X, *oof])
        return self

    def predict_proba(self, X):
        X = np.asarray(X, dtype=float)
        features = X
        for depth, layer in enumerate(self.layers_):
            probs = [est.predict_proba(features) for est in layer]
            if depth == len(self.layers_) - 1:
                return np.mean(probs, axis=0)
            features = np.hstack([X, *probs])
        raise ValueError("Deep Forest ist noch nicht trainiert")

    def predict(self, X):
        return self.classes_[np.argmax(self.predict_proba(X), axis=1)]
