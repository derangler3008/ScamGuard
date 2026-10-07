"""Bildanalyse für Inseratsfotos.

Drei Bausteine (jeder optional, die Pipeline nutzt, was installiert/trainiert ist):

1. Perceptual Hash (pHash) – erkennt bekannte Fake-/Stockfotos wieder, auch wenn sie
   skaliert oder leicht verändert wurden. Betrüger verwenden Bilder oft mehrfach.
2. CLIP Zero-Shot – ohne Training: Ist das Bild ein Stockfoto, ein Screenshot, ein Textbild?
   Passt das Bild zur angegebenen Kategorie (Auto-Inserat mit Handy-Foto)?
3. CNN-Klassifikator (z. B. EfficientNet-B0, Transfer Learning) – lernt aus eigenen
   gelabelten Inseratsbildern. Achtung „schwache Labels“: Jedes Bild erbt das Label seines
   Inserats, obwohl auch Betrugsinserate echt aussehende Fotos haben können.
"""

from __future__ import annotations

from pathlib import Path

from scamguard.config import resolve_path
from scamguard.models.base import TORCH_LOCK, Detector, noisy_or
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
        self._clip_device = "cpu"
        self._prompt_features: dict = {}
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
        """CLIP erst beim ersten Bild laden (ca. 600 MB, Download beim allerersten Mal).
        Die Vergleichstexte werden dabei einmal kodiert – pro Bild bleibt nur die Bildkodierung."""
        if self._clip is not None:
            return True
        if self._clip_failed or not self.cfg.get("use_clip_consistency"):
            return False
        with TORCH_LOCK:  # parallele erste Anfragen: nur einmal laden
            return self._clip is not None or self._load_clip()

    def _load_clip(self) -> bool:
        try:
            from transformers import CLIPModel, CLIPProcessor

            from scamguard.models.text_classifier import best_torch_device

            self._clip_device = best_torch_device()
            self._clip_processor = CLIPProcessor.from_pretrained(self.cfg["clip_model"])
            self._clip = CLIPModel.from_pretrained(self.cfg["clip_model"]).to(self._clip_device).eval()
            self._prompt_features = {name: self._encode_prompts(prompts)
                                     for name, prompts in (("style", STYLE_PROMPTS),
                                                           ("category", CATEGORY_PROMPTS))}
            return True
        except Exception as exc:  # noqa: BLE001 – fehlende Pakete oder kein Netz beim ersten Download
            self._clip = None
            self._clip_failed = True
            self._notes.append(f"CLIP nicht verfügbar: {exc}")
            return False

    @staticmethod
    def _normalized(features):
        # transformers 5 liefert je nach Version Tensor oder Ausgabeobjekt mit pooler_output
        features = getattr(features, "pooler_output", features)
        return features / features.norm(dim=-1, keepdim=True)

    def _encode_prompts(self, prompts: dict[str, str]):
        import torch

        inputs = self._clip_processor(text=list(prompts.values()), return_tensors="pt", padding=True)
        with torch.no_grad():
            features = self._clip.get_text_features(**inputs.to(self._clip_device))
        return list(prompts), self._normalized(features)

    @property
    def available(self) -> bool:
        clip_possible = bool(self.cfg.get("use_clip_consistency")) and not self._clip_failed
        return self._has_imagehash or self._cnn is not None or clip_possible

    @property
    def unavailable_reason(self) -> str | None:
        return "; ".join(self._notes) or None

    # -- Vorhersage -----------------------------------------------------------

    def _clip_probs(self, img) -> dict[str, dict[str, float]]:
        """Eine Bildkodierung, dann Ähnlichkeit zu allen Prompt-Gruppen (wie CLIP-Zero-Shot)."""
        import torch

        inputs = self._clip_processor(images=img, return_tensors="pt")
        with TORCH_LOCK, torch.no_grad():
            image = self._normalized(self._clip.get_image_features(**inputs.to(self._clip_device)))
            scale = self._clip.logit_scale.exp()
            return {name: dict(zip(keys, (scale * image @ texts.T).softmax(dim=-1)[0].tolist()))
                    for name, (keys, texts) in self._prompt_features.items()}

    def _cnn_prob(self, img) -> float:
        import torch

        x = self._cnn_transform(img).unsqueeze(0)
        with TORCH_LOCK, torch.no_grad():
            return float(torch.softmax(self._cnn(x), dim=-1)[0, 1].item())

    def predict(self, listing: Listing) -> ModelResult:
        from PIL import Image

        # Index bezieht sich auf listing.image_paths → die Extension ordnet so das Bild auf der Seite zu
        indexed = [(i, p) for i, p in enumerate(listing.image_paths) if Path(p).exists()]
        if not indexed:
            return ModelResult(self.name, score=None, error="Keine Bilder hochgeladen")

        signals: list[Signal] = []
        cnn_scores: list[float] = []
        clip_ran = False
        for idx, path in indexed:
            label = f"Bild {idx + 1}"  # Dateinamen sind intern (Upload/Temp) → für Menschen nummerieren
            target = f"image:{idx}"
            with Image.open(path) as raw:
                img = raw.convert("RGB")

            if self._has_imagehash and self._known_hashes:
                import imagehash

                h = imagehash.phash(img)
                dist = min(h - known for known in self._known_hashes)
                if dist <= PHASH_MAX_DISTANCE:
                    signals.append(Signal(self.name, "KNOWN_FAKE_IMAGE",
                                          "Bild ist identisch mit einem bekannten Betrugsbild",
                                          0.9, evidence=label, hard=True, target=target))

            if self._ensure_clip():
                clip_ran = True
                probs = self._clip_probs(img)
                style = probs["style"]
                if style["stock"] > 0.6:
                    # Schwacher Hinweis: auch ehrliche Verkäufer fotografieren vor weißem Hintergrund
                    signals.append(Signal(self.name, "STOCK_PHOTO",
                                          "Wirkt wie ein professionelles Produktfoto – evtl. aus dem Netz "
                                          "kopiert (Google-Bilder-Rückwärtssuche empfohlen)",
                                          0.2, evidence=f"{label} ({style['stock']:.0%})", target=target))
                if style["screenshot"] > 0.6 or style["text"] > 0.6:
                    signals.append(Signal(self.name, "SCREENSHOT_OR_TEXT",
                                          "Bild ist ein Screenshot/Textbild statt eines Artikelfotos",
                                          0.25, evidence=label, target=target))
                if listing.category in CATEGORY_PROMPTS:
                    cat = probs["category"]
                    best = max(cat, key=cat.get)
                    if cat[listing.category] < 0.15 and cat[best] > 0.5:
                        signals.append(Signal(self.name, "CATEGORY_MISMATCH",
                                              f"Bild passt nicht zur Kategorie „{listing.category}“ "
                                              f"(sieht eher nach „{best}“ aus)", 0.35, evidence=label,
                                              target=target))

            if self._cnn is not None:
                cnn_scores.append(self._cnn_prob(img))

        if not cnn_scores:
            # Ohne trainiertes CNN liefern Hash-Abgleich und CLIP nur Hinweise, keinen eigenen Score:
            # „nichts Auffälliges im Bild“ ist kein Beleg für Seriosität und würde den Gesamtscore
            # (gewichteter Mittelwert) sonst nach unten ziehen. Harte Treffer wirken über die Fusion.
            note = ("Nur Hinweise (CLIP) – kein trainiertes Bildmodell" if clip_ran
                    else "Kein neuronales Bildmodell aktiv (nur Hash-Abgleich)")
            return ModelResult(self.name, score=None, signals=signals, error=note)

        # Trainiertes CNN: dessen Wahrscheinlichkeit ist der Score, Hinweise ergänzen ihn (Noisy-OR)
        cnn_mean = sum(cnn_scores) / len(cnn_scores)
        return ModelResult(self.name, score=noisy_or([cnn_mean, *(s.weight for s in signals)]),
                           signals=signals)


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
        raise ValueError(f"zu wenige Fotos ({len(train_items)} im Training, {len(val_items)} in der Validierung) – "
                         "Inserate mit Fotos einstufen oder data/datensatz_fuellen_bilder/ füllen")
    if len({y for _, y in train_items}) < 2:
        raise ValueError("Fotos nur von einer Klasse – das Bildmodell braucht Fotos von Betrugs- UND seriösen "
                         "Inseraten")

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
