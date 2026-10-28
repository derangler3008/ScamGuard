"""Qwen mit Fotos: startet den mlx-vlm-Server – ohne Freigabe für fremde Webseiten.

Aufruf (macht `scamguard start`, wenn in config.local.yaml `llm.local.bilder: true` und `vlm_python` stehen):
    .venv-vlm/bin/python scripts/qwen_vlm_server.py --model mlx-community/Qwen3.5-9B-OptiQ-4bit --port 8080

mlx-vlm erlaubt von sich aus Anfragen von jeder Webseite (CORS „*“). Jede geöffnete Seite könnte dann per
JavaScript das lokale Modell benutzen – auslasten oder mit riesigen Anfragen lahmlegen. Der ScamGuard-Server
fragt ohne Browser an und braucht keine Freigabe; deshalb wird CORS hier vor dem Start abgeschaltet.
"""

from fastapi.middleware import cors


class _NoCrossSiteCORS(cors.CORSMiddleware):
    def __init__(self, app, **kwargs):
        kwargs.update(allow_origins=[], allow_origin_regex=None, allow_credentials=False)
        super().__init__(app, **kwargs)


cors.CORSMiddleware = _NoCrossSiteCORS  # muss vor dem Import von mlx_vlm.server passieren

from mlx_vlm.server import main

if __name__ == "__main__":
    main()
