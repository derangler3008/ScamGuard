"""Text-Klassifikation (Betrug vs. legitim).

Zwei Stufen:
1. `TextBaselineDetector`   – TF-IDF (Wort- + Zeichen-n-Gramme) + logistische Regression.
   Trainiert in Sekunden auf der CPU. Zeichen-n-Gramme fangen auch Tippfehler und
   „gebrochenes Deutsch“ ein. Messlatte für alle weiteren Modelle.
2. `TextTransformerDetector` – Feintuning eines deutschen BERT (Standard: deepset/gbert-base).
   Braucht `pip install -e ".[text]"`, idealerweise GPU (Colab/bwUniCluster) oder Apple MPS.
"""

from __future__ import annotations

import json
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

# Reine Chats sehen anders aus als Inserate (kein Titel, andere Wörter). Ein Textmodell, das kaum Chats
# gesehen hat, rät dort – z. B. war „Hallo“ in den Demo-Daten fast nur in Betrugsfällen. Darum urteilt
# es über reine Chats erst, wenn es mindestens so viele Chat-Beispiele JE KLASSE gelernt hat.
MIN_CHAT_EXAMPLES = 20
CHAT_META_FILE = "scamguard_meta.json"  # Transformer: Zusatzinfos neben den Gewichten


def chat_counts(listings: list[Listing]) -> dict[str, int]:
    chats = [l for l in listings if l.chat_only]
    return {"betrug": sum(l.label == 1 for l in chats), "serioes": sum(l.label == 0 for l in chats)}


def chat_abstention(listing: Listing, counts: dict[str, int] | None, name: str) -> ModelResult | None:
    """Kein Urteil über reine Chats, wenn das Modell zu wenige davon kennt (sonst None)."""
    if not listing.chat_only or min((counts or {}).values(), default=0) >= MIN_CHAT_EXAMPLES:
        return None
    counts = counts or {"betrug": 0, "serioes": 0}
    return ModelResult(name, score=None, error=(
        f"Kennt zu wenige reine Chats ({counts.get('betrug', 0)} Betrug, {counts.get('serioes', 0)} seriös; "
        f"nötig: je {MIN_CHAT_EXAMPLES}) – urteilt hier nicht. Chats labeln, dann `scamguard retrain`."))


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
        self._names = None  # Merkmalsnamen – einmal pro geladenem Modell, nicht pro Scan
        self._mtime: float | None = None
        self._error: str | None = None
        self._maybe_reload()

    def _maybe_reload(self) -> None:
        """Lädt das Modell (neu), wenn die Datei neu ist – `scamguard retrain` wirkt ohne Neustart."""
        try:
            mtime = self.path.stat().st_mtime
        except FileNotFoundError:
            return
        if mtime == self._mtime:
            return
        try:
            pipe = joblib.load(self.path)
        except Exception as exc:  # noqa: BLE001 – beschädigte/inkompatible Datei
            self._error = f"Baseline konnte nicht geladen werden: {exc}"
            return
        self._pipe, self._mtime, self._error = pipe, mtime, None
        self._names = pipe.named_steps["features"].get_feature_names_out()

    @property
    def available(self) -> bool:
        self._maybe_reload()
        return self._pipe is not None

    @property
    def unavailable_reason(self) -> str | None:
        return self._error or "Noch nicht trainiert → `scamguard retrain`"

    def predict(self, listing: Listing) -> ModelResult:
        text = listing.full_text
        if not text:
            return ModelResult(self.name, score=None, error="Kein Text im Inserat")
        if abstain := chat_abstention(listing, getattr(self._pipe, "scamguard_chat_counts", None), self.name):
            return abstain
        x = self._pipe.named_steps["features"].transform([text])  # nur einmal vektorisieren
        prob = float(self._pipe.named_steps["clf"].predict_proba(x)[0][1])
        return ModelResult(name=self.name, score=prob, signals=self._explain(x))

    def _explain(self, x, top_k: int = 3) -> list[Signal]:
        """Welche Wörter/Wortpaare haben den Score am stärksten nach oben gedrückt?

        Nur Wort-n-Gramme (Zeichenfragmente wie „ zah“ sind für Menschen unlesbar) und
        keine reinen Stoppwörter („ich“, „das“) – die sind statistisch, aber nicht erklärend.
        """
        coef = self._pipe.named_steps["clf"].coef_[0]
        x = x.tocoo()
        contrib = x.data * coef[x.col]
        names = self._names
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


def with_text(listings: list[Listing]) -> list[Listing]:
    """Nur Inserate mit Text – reine Bild-Datensätze gehören nicht ins Texttraining."""
    return [l for l in listings if l.full_text]


def train_text_baseline(train: list[Listing], cfg: dict) -> Path:
    train = with_text(train)
    if len({l.label for l in train}) < 2:
        raise ValueError("Für das Textmodell braucht es Beispiele für Betrug UND seriös")
    pipe = build_baseline_pipeline()
    pipe.fit([l.full_text for l in train], [int(l.label) for l in train])
    pipe.scamguard_chat_counts = chat_counts(train)  # wird mitgespeichert (joblib)
    out = resolve_path(cfg["text_model"]["baseline_path"])
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")  # atomar ersetzen: der laufende Server liest nie eine halbe Datei
    joblib.dump(pipe, tmp)
    tmp.replace(out)
    return out


# --------------------------------------------------------------------------- Transformer

class TextTransformerDetector(Detector):
    name = "text_model"

    def __init__(self, cfg: dict):
        self.path = resolve_path(cfg["text_model"]["path"])
        self.max_length = int(cfg["text_model"]["max_length"])
        self._model = self._tokenizer = None
        self._chat_counts: dict[str, int] | None = None
        self._error: str | None = None
        if (self.path / "config.json").exists():
            try:
                from transformers import AutoModelForSequenceClassification, AutoTokenizer

                self._device = best_torch_device()
                self._tokenizer = AutoTokenizer.from_pretrained(self.path)
                self._model = AutoModelForSequenceClassification.from_pretrained(self.path)
                self._model.to(self._device).eval()
                meta = self.path / CHAT_META_FILE
                self._chat_counts = json.loads(meta.read_text(encoding="utf-8")) if meta.exists() else None
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

        if abstain := chat_abstention(listing, self._chat_counts, self.name):
            return abstain
        enc = self._tokenizer(listing.full_text, truncation=True, max_length=self.max_length,
                              return_tensors="pt").to(self._device)
        with torch.no_grad():
            logits = self._model(**enc).logits
        prob = float(torch.softmax(logits, dim=-1)[0, 1].item())
        return ModelResult(name=self.name, score=prob)


def load_pretrained(name: str, **model_kwargs):
    """Tokenizer und Klassifikationsmodell laden.

    transformers 5 erkennt ältere Hub-Modelle nicht mehr automatisch, wenn ihrer config.json das Feld
    `model_type` und dem Repo eine tokenizer.json fehlt – so bei deepset/gbert-base (Fehlermeldung
    „Unrecognized model“ bzw. irreführend „sentencepiece or tiktoken“). Für BERT-Modelle werden dann
    die BERT-Klassen direkt genutzt; das trainierte Modell wird vollständig gespeichert und lädt danach
    wieder über die Auto-Klassen."""
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    try:
        return (AutoTokenizer.from_pretrained(name),
                AutoModelForSequenceClassification.from_pretrained(name, **model_kwargs))
    except ValueError:
        from transformers import BertForSequenceClassification, BertTokenizer, PretrainedConfig

        config, _ = PretrainedConfig.get_config_dict(name)
        is_old_bert = not config.get("model_type") and any(
            a.startswith("Bert") for a in config.get("architectures") or [])
        if not is_old_bert:
            raise

        return (BertTokenizer.from_pretrained(name),
                BertForSequenceClassification.from_pretrained(name, **model_kwargs))


def train_text_transformer(train: list[Listing], val: list[Listing], cfg: dict) -> Path:
    import torch
    from datasets import Dataset
    from sklearn.metrics import f1_score, precision_score, recall_score
    from transformers import DataCollatorWithPadding, Trainer, TrainingArguments

    train, val = with_text(train), with_text(val)
    tc = cfg["text_model"]
    out = resolve_path(tc["path"])
    tokenizer, model = load_pretrained(
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
    (out / CHAT_META_FILE).write_text(json.dumps(chat_counts(train)), encoding="utf-8")
    return out
