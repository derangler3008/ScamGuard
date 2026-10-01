"""Bild- und LLM-Detektor ohne schwere Abhängigkeiten bzw. ohne echten API-Aufruf testen."""

import json
from types import SimpleNamespace

import pytest
from PIL import Image

from scamguard.config import load_config, with_overrides
from scamguard.models.image_model import ImageDetector, phash_of
from scamguard.models.llm_judge import LLMJudgeDetector, _final_text
from scamguard.schema import Listing

CFG = load_config()


# --------------------------------------------------------------------------- Bild

@pytest.fixture
def fake_image(tmp_path):
    img = Image.new("RGB", (64, 64), "white")
    for x in range(32):  # Muster, damit der pHash nicht trivial ist
        for y in range(64):
            img.putpixel((x, y), (200, 30, 30))
    path = tmp_path / "fake.jpg"
    img.save(path)
    return path


def test_known_fake_image_is_hard_signal(fake_image, tmp_path):
    pytest.importorskip("imagehash")
    hashes = tmp_path / "hashes.txt"
    hashes.write_text(f"# Testbild\n{phash_of(fake_image)}\n", encoding="utf-8")
    cfg = with_overrides(CFG, {"image_model": {"known_fake_hashes": str(hashes),
                                               "use_clip_consistency": False,
                                               "path": str(tmp_path / "kein_modell.pt")}})
    result = ImageDetector(cfg).predict(Listing(title="x", image_paths=[str(fake_image)]))
    assert result.score is None  # nur Hash-Abgleich → kein eigener Score
    fake = next(s for s in result.signals if s.code == "KNOWN_FAKE_IMAGE")
    assert fake.hard and fake.target == "image:0" and fake.evidence == "Bild 1"


def test_image_detector_without_images_returns_no_score():
    result = ImageDetector(CFG).predict(Listing(title="ohne Bild"))
    assert result.score is None and "Keine Bilder" in result.error


# --------------------------------------------------------------------------- LLM

def _response(blocks, stop_reason="end_turn"):
    return SimpleNamespace(content=blocks, stop_reason=stop_reason)


def _text(t):
    return SimpleNamespace(type="text", text=t)


VALID = {"scam_probability": 0.92, "scam_type": "fake_zahlungslink", "language_quality": "gebrochen",
         "red_flags": [{"code": "fake_link", "explanation": "Fake-Zahlungslink", "evidence": "klicken Sie"}],
         "summary": "Typische Sicher-bezahlen-Masche."}


class FakeClient:
    def __init__(self, response):
        self._response = response
        self.messages = SimpleNamespace(create=lambda **kw: self._capture(kw))
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=lambda **kw: self._capture(kw)))
        self.last_request = None

    def _capture(self, kwargs):
        self.last_request = kwargs
        return self._response


def _judge(response):
    """Claude-Backend mit Fake-Client (kein echter API-Aufruf)."""
    pytest.importorskip("anthropic")
    det = LLMJudgeDetector(with_overrides(CFG, {"llm": {"enabled": True, "provider": "anthropic"}}))
    det.backend._client = FakeClient(response)
    return det


def test_final_text_uses_only_text_after_last_fallback():
    resp = _response([_text("{abgebrochen"), SimpleNamespace(type="fallback"), _text('{"ok": 1}')])
    assert _final_text(resp) == '{"ok": 1}'


def test_llm_parses_structured_output_and_minimizes_data():
    det = _judge(_response([_text(json.dumps(VALID))]))
    listing = Listing(title="PS5", description="klicken Sie hier", seller_name="Max Muster",
                      location="Mannheim")
    result = det.predict(listing)
    assert result.score == pytest.approx(0.92)
    assert any(s.code == "LLM_FAKE_LINK" for s in result.signals)
    sent = json.dumps(det.backend._client.last_request, ensure_ascii=False)
    assert "Max Muster" not in sent and "Mannheim" not in sent  # Datensparsamkeit
    assert det.backend._client.last_request["output_config"]["format"]["type"] == "json_schema"


def test_llm_refusal_and_truncation_are_reported_not_raised():
    assert "abgelehnt" in _judge(_response([], "refusal")).predict(Listing(title="x")).error
    assert "abgeschnitten" in _judge(_response([], "max_tokens")).predict(Listing(title="x")).error


def test_llm_score_is_clamped():
    det = _judge(_response([_text(json.dumps({**VALID, "scam_probability": 1.7}))]))
    assert det.predict(Listing(title="x")).score == 1.0


# --------------------------------------------------------------------------- lokales LLM (Qwen)

httpx = pytest.importorskip("httpx")


def _local(handler):
    """Lokales Backend gegen einen simulierten OpenAI-kompatiblen Server."""
    det = LLMJudgeDetector(with_overrides(CFG, {"llm": {"enabled": True, "provider": "local"}}))
    det.backend._transport = httpx.MockTransport(handler)
    return det


def _chat_reply(content: str, finish_reason: str = "stop") -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": content},
                                                  "finish_reason": finish_reason}]})


def test_local_llm_parses_reply_and_disables_thinking():
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        # Lokale Modelle verpacken JSON gern in Denk-Blöcke oder Code-Zäune
        return _chat_reply(f"<think>kurz</think>\n```json\n{json.dumps(VALID)}\n```")

    listing = Listing(title="PS5", description="klicken Sie hier", seller_name="Max Muster", location="Mannheim")
    result = _local(handler).predict(listing)
    assert result.score == pytest.approx(0.92)
    summary = next(s for s in result.signals if s.code == "LLM_SUMMARY")
    assert "Qwen3.5-9B" in summary.message  # Modell wird in der Begründung genannt
    body = requests[0]
    assert body["chat_template_kwargs"] == {"enable_thinking": False}
    assert body["response_format"]["type"] == "json_schema"
    sent = json.dumps(body, ensure_ascii=False)
    assert "Max Muster" not in sent and "Mannheim" not in sent


def test_local_llm_retries_without_response_format_and_after_invalid_json():
    calls = []

    def handler(request):
        body = json.loads(request.content)
        calls.append(body)
        if "response_format" in body:
            return httpx.Response(400, json={"error": "unknown field response_format"})
        if len(calls) == 2:
            return _chat_reply("Das Inserat wirkt verdächtig.")  # kein JSON → erneuter Versuch
        return _chat_reply(json.dumps({**VALID, "scam_probability": "85 %"}))

    result = _local(handler).predict(Listing(title="x"))
    assert result.score == pytest.approx(0.85)
    assert len(calls) == 3 and calls[-1]["temperature"] == 0.0


def test_local_llm_unreachable_or_truncated_is_reported_not_raised():
    def offline(request):
        raise httpx.ConnectError("Verbindung abgelehnt", request=request)

    assert "nicht erreichbar" in _local(offline).predict(Listing(title="x")).error
    truncated = _local(lambda request: _chat_reply('{"scam_probability": 0.4', "length"))
    assert "abgeschnitten" in truncated.predict(Listing(title="x")).error


def test_parse_judgement_normalizes_unknown_values():
    from scamguard.models.llm_judge import parse_judgement

    data = parse_judgement('{"scam_probability": 0.3, "scam_type": "betrug!", "language_quality": "ok",'
                           ' "red_flags": [{"code": "Link Prüfen!", "explanation": "x", "evidence": ""},'
                           ' "kaputt"], "summary": "s"}')
    assert data["scam_type"] == "fake_inserat_sonstiges"
    assert data["language_quality"] == "unbekannt"
    assert data["red_flags"] == [{"code": "link_prüfen", "explanation": "x", "evidence": ""}]
    with pytest.raises(ValueError):
        parse_judgement("Kein JSON hier")
