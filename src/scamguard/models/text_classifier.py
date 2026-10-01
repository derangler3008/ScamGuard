"""Text-Klassifikation (Betrug vs. legitim).

Zwei Stufen:
1. `TextBaselineDetector`   – TF-IDF (Wort- + Zeichen-n-Gramme) + logistische Regression.
   Trainiert in Sekunden auf der CPU. Zeichen-n-Gramme fangen auch Tippfehler und
   „gebrochenes Deutsch“ ein. Gut als erste Messlatte für den Projektbericht.
2. `TextTransformerDetector` – Feintuning eines deutschen BERT (Standard: deepset/gbert-base).
   Braucht `pip install -e ".[text]"`, idealerweise GPU (Colab/bwUniCluster) oder Apple MPS.
"""

from __future__ import annotations

from pathlib import Path

import joblib
import numpy as np

from scamguard.config import resolve_path
from scamguard.features.language import GERMAN_STOPWORDS
from scamguard.models.base import Detector
from scamguard.schema import Listing, ModelResult, Signal


def best_torch_device() -> str:
    import torch

    if torch.cuda.is_available():
        return "cuda"
    if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
        return "mps"
    return "cpu"


# --------------------------------------------------------------------------- Baseline

def build_baseline_pipeline():
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import FeatureUnion, Pipeline

    features = FeatureUnion([
        ("word", TfidfVectorizer(ngram_range=(1, 2), min_df=2, sublinear_tf=True, lowercase=True)),
        ("char", TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), min_df=2, sublinear_tf=True)),
    ])
    clf = LogisticRegression(max_iter=2000, class_weight="balanced", C=4.0)
    return Pipeline([("features", features), ("clf", clf)])


class TextBaselineDetector(Detector):
    name = "text_model"

    def __init__(self, cfg: dict):
        self.path = resolve_path(cfg["text_model"]["baseline_path"])
        self._pipe = None
        self._error: str | None = None
        if self.path.exists():
            try:
                self._pipe = joblib.load(self.path)
            except Exception as exc:  # noqa: BLE001 – beschädigte/inkompatible Datei
                self._error = f"Baseline konnte nicht geladen werden: {exc}"

    @property
    def available(self) -> bool:
        return self._pipe is not None

    @property
    def unavailable_reason(self) -> str | None:
        return self._error or "Noch nicht trainiert → `scamguard train text-baseline`"

    def predict(self, listing: Listing) -> ModelResult:
        text = listing.full_text
        prob = float(self._pipe.predict_proba([text])[0][1])
        return ModelResult(name=self.name, score=prob, signals=self._explain(text))

    def _explain(self, text: str, top_k: int = 3) -> list[Signal]:
        """Welche Wörter/Wortpaare haben den Score am stärksten nach oben gedrückt?

        Nur Wort-n-Gramme (Zeichenfragmente wie „ zah“ sind für Menschen unlesbar) und
        keine reinen Stoppwörter („ich“, „das“) – die sind statistisch, aber nicht erklärend.
        """
        union = self._pipe.named_steps["features"]
        coef = self._pipe.named_steps["clf"].coef_[0]
        x = union.transform([text]).tocoo()
        contrib = x.data * coef[x.col]
        names = union.get_feature_names_out()
        signals = []
        for idx in np.argsort(contrib)[::-1]:
            if contrib[idx] <= 0.05 or len(signals) >= top_k:
                break
            prefix, _, ngram = names[x.col[idx]].partition("__")
            if prefix != "word" or len(ngram) < 4 or all(w in GERMAN_STOPWORDS for w in ngram.split()):
                continue
            signals.append(Signal("text_model", "TEXT_PATTERN",
                                  f"Textmuster, das in Betrugsfällen der Trainingsdaten häufig ist: „{ngram}“",
                                  float(min(contrib[idx], 0.5)), evidence=ngram, highlights=[ngram]))
        return signals


def train_text_baseline(train: list[Listing], cfg: dict) -> Path:
    pipe = build_baseline_pipeline()
    pipe.fit([l.full_text for l in train], [int(l.label) for l in train])
    out = resolve_path(cfg["text_model"]["baseline_path"])
    out.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(pipe, out)
    return out


# --------------------------------------------------------------------------- Transformer

class TextTransformerDetector(Detector):
    name = "text_model"

    def __init__(self, cfg: dict):
        self.path = resolve_path(cfg["text_model"]["path"])
        self.max_length = int(cfg["text_model"]["max_length"])
        self._model = self._tokenizer = None
        self._error: str | None = None
        if (self.path / "config.json").exists():
            try:
                from transformers import AutoModelForSequenceClassification, AutoTokenizer

                self._device = best_torch_device()
                self._tokenizer = AutoTokenizer.from_pretrained(self.path)
                self._model = AutoModelForSequenceClassification.from_pretrained(self.path)
                self._model.to(self._device).eval()
            except ImportError:
                self._error = 'transformers/torch fehlen → pip install -e ".[text]"'
            except Exception as exc:  # noqa: BLE001 – bewusste Robustheitsgrenze
                self._error = f"Transformer konnte nicht geladen werden: {exc}"

    @property
    def available(self) -> bool:
        return self._model is not None

    @property
    def unavailable_reason(self) -> str | None:
        return self._error or "Noch nicht trainiert → `scamguard train text-transformer`"

    def predict(self, listing: Listing) -> ModelResult:
        import torch

        enc = self._tokenizer(listing.full_text, truncation=True, max_length=self.max_length,
                              return_tensors="pt").to(self._device)
        with torch.no_grad():
            logits = self._model(**enc).logits
        prob = float(torch.softmax(logits, dim=-1)[0, 1].item())
        return ModelResult(name=self.name, score=prob)


def train_text_transformer(train: list[Listing], val: list[Listing], cfg: dict) -> Path:
    import torch
    from datasets import Dataset
    from sklearn.metrics import f1_score, precision_score, recall_score
    from transformers import (
        AutoModelForSequenceClassification,
        AutoTokenizer,
        DataCollatorWithPadding,
        Trainer,
        TrainingArguments,
    )

    tc = cfg["text_model"]
    out = resolve_path(tc["path"])
    tokenizer = AutoTokenizer.from_pretrained(tc["base_model"])
    model = AutoModelForSequenceClassification.from_pretrained(
        tc["base_model"], num_labels=2, id2label={0: "legit", 1: "scam"}, label2id={"legit": 0, "scam": 1},
    )

    def to_dataset(listings: list[Listing]) -> Dataset:
        ds = Dataset.from_dict({"text": [l.full_text for l in listings],
                                "label": [int(l.label) for l in listings]})
        return ds.map(lambda b: tokenizer(b["text"], truncation=True, max_length=int(tc["max_length"])),
                      batched=True, remove_columns=["text"])

    # Betrugsfälle sind meist in der Minderheit → Klassen gewichten
    labels = np.array([int(l.label) for l in train])
    counts = np.bincount(labels, minlength=2).astype(float)
    class_weights = torch.tensor(counts.sum() / (2 * np.maximum(counts, 1)), dtype=torch.float)

    class WeightedTrainer(Trainer):
        def compute_loss(self, model, inputs, return_outputs=False, **kwargs):
            labels_t = inputs.pop("labels")
            outputs = model(**inputs)
            loss = torch.nn.functional.cross_entropy(
                outputs.logits, labels_t, weight=class_weights.to(outputs.logits.device))
            return (loss, outputs) if return_outputs else loss

    def compute_metrics(eval_pred):
        logits, y = eval_pred
        pred = logits.argmax(-1)
        return {"f1": f1_score(y, pred, zero_division=0),
                "precision": precision_score(y, pred, zero_division=0),
                "recall": recall_score(y, pred, zero_division=0)}

    args = TrainingArguments(
        output_dir=str(out / "checkpoints"),
        num_train_epochs=float(tc["epochs"]),
        per_device_train_batch_size=int(tc["batch_size"]),
        per_device_eval_batch_size=int(tc["batch_size"]),
        learning_rate=float(tc["learning_rate"]),
        weight_decay=0.01,
        eval_strategy="epoch",
        save_strategy="epoch",
        save_total_limit=1,
        load_best_model_at_end=True,
        metric_for_best_model="f1",
        logging_steps=20,
        report_to="none",
    )
    trainer = WeightedTrainer(
        model=model, args=args,
        train_dataset=to_dataset(train), eval_dataset=to_dataset(val),
        data_collator=DataCollatorWithPadding(tokenizer),
        compute_metrics=compute_metrics,
    )
    trainer.train()
    trainer.save_model(str(out))
    tokenizer.save_pretrained(str(out))
    return out
