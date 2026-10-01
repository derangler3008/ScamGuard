"""Bildanalyse für Inseratsfotos.

Drei Bausteine (jeder optional, die Pipeline nutzt, was installiert/trainiert ist):

1. Perceptual Hash (pHash) – erkennt bekannte Fake-/Stockfotos wieder, auch wenn sie
   skaliert oder leicht verändert wurden. Betrüger verwenden Bilder oft mehrfach.
2. CLIP Zero-Shot – ohne Training: Ist das Bild ein Stockfoto, ein Screenshot, ein Textbild?
   Passt das Bild zur angegebenen Kategorie (Auto-Inserat mit Handy-Foto)?
3. CNN-Klassifikator (z. B. EfficientNet-B0, Transfer Learning) – lernt aus euren
   gelabelten Inseratsbildern. Achtung „schwache Labels“: Jedes Bild erbt das Label seines
   Inserats, obwohl auch Betrugsinserate echt aussehende Fotos haben können.
"""

from __future__ import annotations

from pathlib import Path

from scamguard.config import resolve_path
from scamguard.models.base import Detector, noisy_or
from scamguard.schema import Listing, ModelResult, Signal

PHASH_MAX_DISTANCE = 6  # Hamming-Distanz (von 64 Bit), ab der zwei Bilder als „gleich“ gelten

# CLIP ist überwiegend englisch trainiert → englische Prompts liefern bessere Ergebnisse
STYLE_PROMPTS = {
    "real": "an amateur smartphone photo of a used item at someone's home",
    "stock": "a professional product photo on a plain white background",
    "screenshot": "a screenshot of a website or smartphone app",
    "text": "an image that mainly shows text",
}
CATEGORY_PROMPTS = {
    "elektronik": "a photo of an electronic device such as a phone, laptop, or game console",
    "haushaltsgeraete": "a photo of a household appliance such as a washing machine or fridge",
    "auto": "a photo of a car",
    "moebel": "a photo of furniture",
    "mode": "a photo of clothes, shoes, or a handbag",
    "tiere": "a photo of an animal",
    "immobilien": "a photo of a room, apartment, or house",
    "tickets": "a photo of a concert or event ticket",
}


def phash_of(path: str | Path) -> str:
    import imagehash
    from PIL import Image

    with Image.open(path) as img:
        return str(imagehash.phash(img.convert("RGB")))


def _replace_head(model, num_classes: int):
    """Ersetzt die letzte Schicht eines torchvision-Modells durch eine 2-Klassen-Schicht."""
    from torch import nn

    if hasattr(model, "fc"):  # ResNet & Co.
        model.fc = nn.Linear(model.fc.in_features, num_classes)
    elif hasattr(model, "classifier"):  # EfficientNet, MobileNet, ConvNeXt
        last = model.classifier[-1]
        model.classifier[-1] = nn.Linear(last.in_features, num_classes)
    else:
        raise ValueError("Unbekannte Modellarchitektur – Kopf manuell ersetzen")
    return model


class ImageDetector(Detector):
    name = "image_model"

    def __init__(self, cfg: dict):
        ic = cfg["image_model"]
        self.cfg = ic
        self._known_hashes = []
        self._cnn = self._cnn_transform = None
        self._clip = self._clip_processor = None
        self._clip_failed = False
        self._notes: list[str] = []

        try:
            import imagehash

            hash_file = resolve_path(ic["known_fake_hashes"])
            if hash_file.exists():
                for line in hash_file.read_text(encoding="utf-8").splitlines():
                    line = line.split("#", 1)[0].strip()
                    if line:
                        self._known_hashes.append(imagehash.hex_to_hash(line))
            self._has_imagehash = True
        except ImportError:
            self._has_imagehash = False
            self._notes.append('imagehash fehlt → pip install -e ".[vision]"')

        model_path = resolve_path(ic["path"])
        if model_path.exists():
            try:
                self._load_cnn(model_path)
            except ImportError:
                self._notes.append('torch/torchvision fehlen → pip install -e ".[vision]"')
            except Exception as exc:  # noqa: BLE001 – bewusste Robustheitsgrenze
                self._notes.append(f"CNN konnte nicht geladen werden: {exc}")

    # -- Laden ----------------------------------------------------------------

    def _load_cnn(self, path: Path) -> None:
        import torch
        import torchvision

        ckpt = torch.load(path, map_location="cpu", weights_only=False)
        model = torchvision.models.get_model(ckpt["backbone"], weights=None)
        model = _replace_head(model, 2)
        model.load_state_dict(ckpt["state_dict"])
        self._cnn = model.eval()
        weights = torchvision.models.get_model_weights(ckpt["backbone"]).DEFAULT
        self._cnn_transform = weights.transforms()

    def _ensure_clip(self) -> bool:
        """CLIP erst beim ersten Bild laden (ca. 600 MB, Download beim allerersten Mal)."""
        if self._clip is not None:
            return True
        if self._clip_failed or not self.cfg.get("use_clip_consistency"):
            return False
        try:
            from transformers import CLIPModel, CLIPProcessor

            self._clip = CLIPModel.from_pretrained(self.cfg["clip_model"]).eval()
            self._clip_processor = CLIPProcessor.from_pretrained(self.cfg["clip_model"])
            return True
        except Exception as exc:  # noqa: BLE001 – fehlende Pakete oder kein Netz beim ersten Download
            self._clip_failed = True
            self._notes.append(f"CLIP nicht verfügbar: {exc}")
            return False

    @property
    def available(self) -> bool:
        clip_possible = bool(self.cfg.get("use_clip_consistency")) and not self._clip_failed
        return self._has_imagehash or self._cnn is not None or clip_possible

    @property
    def unavailable_reason(self) -> str | None:
        return "; ".join(self._notes) or None

    # -- Vorhersage -----------------------------------------------------------

    def _clip_probs(self, img, prompts: dict[str, str]) -> dict[str, float]:
        import torch

        inputs = self._clip_processor(text=list(prompts.values()), images=img,
                                      return_tensors="pt", padding=True)
        with torch.no_grad():
            probs = self._clip(**inputs).logits_per_image.softmax(dim=1)[0].tolist()
        return dict(zip(prompts.keys(), probs))

    def _cnn_prob(self, img) -> float:
        import torch

        x = self._cnn_transform(img).unsqueeze(0)
        with torch.no_grad():
            return float(torch.softmax(self._cnn(x), dim=-1)[0, 1].item())

    def predict(self, listing: Listing) -> ModelResult:
        from PIL import Image

        paths = [p for p in listing.image_paths if Path(p).exists()]
        if not paths:
            return ModelResult(self.name, score=None, error="Keine Bilder hochgeladen")

        signals: list[Signal] = []
        cnn_scores: list[float] = []
        neural_ran = False
        for path in paths:
            label = Path(path).name
            with Image.open(path) as raw:
                img = raw.convert("RGB")

            if self._has_imagehash and self._known_hashes:
                import imagehash

                h = imagehash.phash(img)
                dist = min(h - known for known in self._known_hashes)
                if dist <= PHASH_MAX_DISTANCE:
                    signals.append(Signal(self.name, "KNOWN_FAKE_IMAGE",
                                          "Bild ist identisch mit einem bekannten Betrugsbild",
                                          0.9, evidence=label, hard=True))

            if self._ensure_clip():
                neural_ran = True
                style = self._clip_probs(img, STYLE_PROMPTS)
                if style["stock"] > 0.6:
                    signals.append(Signal(self.name, "STOCK_PHOTO",
                                          "Wirkt wie ein professionelles Produktfoto – evtl. aus dem Netz "
                                          "kopiert (Google-Bilder-Rückwärtssuche empfohlen)",
                                          0.3, evidence=f"{label} ({style['stock']:.0%})"))
                if style["screenshot"] > 0.6 or style["text"] > 0.6:
                    signals.append(Signal(self.name, "SCREENSHOT_OR_TEXT",
                                          "Bild ist ein Screenshot/Textbild statt eines Artikelfotos",
                                          0.25, evidence=label))
                if listing.category in CATEGORY_PROMPTS:
                    cat = self._clip_probs(img, CATEGORY_PROMPTS)
                    best = max(cat, key=cat.get)
                    if cat[listing.category] < 0.15 and cat[best] > 0.5:
                        signals.append(Signal(self.name, "CATEGORY_MISMATCH",
                                              f"Bild passt nicht zur Kategorie „{listing.category}“ "
                                              f"(sieht eher nach „{best}“ aus)", 0.35, evidence=label))

            if self._cnn is not None:
                neural_ran = True
                cnn_scores.append(self._cnn_prob(img))

        if not neural_ran:
            # Nur Hash-Abgleich → kein eigener Score, harte Treffer wirken trotzdem über die Fusion
            return ModelResult(self.name, score=None, signals=signals,
                               error="Kein neuronales Bildmodell aktiv (nur Hash-Abgleich)")

        signal_score = noisy_or([s.weight for s in signals])
        if cnn_scores:
            cnn_mean = sum(cnn_scores) / len(cnn_scores)
            score = 0.6 * cnn_mean + 0.4 * signal_score
        else:
            score = signal_score
        return ModelResult(self.name, score=score, signals=signals)


# --------------------------------------------------------------------------- Training

def train_image_model(train: list[Listing], val: list[Listing], cfg: dict) -> Path:
    """Transfer Learning: vortrainiertes CNN, neuer 2-Klassen-Kopf, komplettes Feintuning."""
    import torch
    import torchvision
    from PIL import Image
    from sklearn.metrics import f1_score
    from torch.utils.data import DataLoader, Dataset
    from torchvision import transforms as T

    from scamguard.models.text_classifier import best_torch_device

    ic = cfg["image_model"]
    weights = torchvision.models.get_model_weights(ic["backbone"]).DEFAULT
    eval_tf = weights.transforms()
    train_tf = T.Compose([
        T.RandomResizedCrop(224, scale=(0.7, 1.0)),
        T.RandomHorizontalFlip(),
        T.ColorJitter(0.2, 0.2, 0.2),
        T.ToTensor(),
        T.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
    ])

    def pairs(listings: list[Listing]) -> list[tuple[str, int]]:
        return [(p, int(l.label)) for l in listings for p in l.image_paths if Path(p).exists()]

    class ImageSet(Dataset):
        def __init__(self, items, tf):
            self.items, self.tf = items, tf

        def __len__(self):
            return len(self.items)

        def __getitem__(self, i):
            path, label = self.items[i]
            with Image.open(path) as img:
                return self.tf(img.convert("RGB")), label

    train_items, val_items = pairs(train), pairs(val)
    if not train_items or not val_items:
        raise ValueError("Keine Bilder in Train/Val gefunden – image_paths in den Datensätzen prüfen")

    device = best_torch_device()
    model = _replace_head(torchvision.models.get_model(ic["backbone"], weights=weights), 2).to(device)
    train_dl = DataLoader(ImageSet(train_items, train_tf), batch_size=int(ic["batch_size"]), shuffle=True)
    val_dl = DataLoader(ImageSet(val_items, eval_tf), batch_size=int(ic["batch_size"]))

    labels = torch.tensor([y for _, y in train_items])
    counts = torch.bincount(labels, minlength=2).float().clamp(min=1)
    loss_fn = torch.nn.CrossEntropyLoss(weight=(counts.sum() / (2 * counts)).to(device))
    optim = torch.optim.AdamW(model.parameters(), lr=float(ic["learning_rate"]))

    out = resolve_path(ic["path"])
    out.parent.mkdir(parents=True, exist_ok=True)
    best_f1 = -1.0
    for epoch in range(int(ic["epochs"])):
        model.train()
        for x, y in train_dl:
            optim.zero_grad()
            loss = loss_fn(model(x.to(device)), y.to(device))
            loss.backward()
            optim.step()

        model.eval()
        preds, gold = [], []
        with torch.no_grad():
            for x, y in val_dl:
                preds += model(x.to(device)).argmax(-1).cpu().tolist()
                gold += y.tolist()
        f1 = f1_score(gold, preds, zero_division=0)
        print(f"Epoche {epoch + 1}: Val-F1 = {f1:.3f}")
        if f1 > best_f1:
            best_f1 = f1
            state = {k: v.detach().cpu() for k, v in model.state_dict().items()}
            torch.save({"backbone": ic["backbone"], "state_dict": state}, out)
    return out
