import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
PYTHON_BRIDGE_PATH = ROOT / "packages" / "python-bridge"
if str(PYTHON_BRIDGE_PATH) not in sys.path:
    sys.path.insert(0, str(PYTHON_BRIDGE_PATH))

from app.cors import build_cors_middleware_options  # noqa: E402


def create_client(cors_origins: str) -> TestClient:
    app = FastAPI()
    app.add_middleware(
        CORSMiddleware,
        **build_cors_middleware_options(
            cors_origins,
            r"^https://([a-z0-9_-]+\.)?(1ink\.us|noahcohn\.com)$|^http://(localhost|127\.0\.0\.1)(:\d+)?$",
        ),
    )

    @app.post("/api/share")
    async def create_share() -> dict:
        return {"ok": True}

    return TestClient(app)


def test_preflight_allows_known_domain_family_origin():
    response = create_client("https://storage.noahcohn.com").options(
        "/api/share",
        headers={
            "Origin": "https://test.1ink.us",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "https://test.1ink.us"


def test_preflight_rejects_unknown_origin():
    response = create_client("https://storage.noahcohn.com").options(
        "/api/share",
        headers={
            "Origin": "https://evil.example",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )

    assert response.status_code == 400


def test_app_preflight_allows_patch():
    from app.main import app

    response = TestClient(app).options(
        "/api/songs/abc",
        headers={
            "Origin": "https://test.1ink.us",
            "Access-Control-Request-Method": "PATCH",
            "Access-Control-Request-Headers": "content-type",
        },
    )

    assert response.status_code == 204
    methods = response.headers.get("access-control-allow-methods", "")
    assert "PATCH" in methods
    _assert_single_acao(response, expected=None)


def _acao_header_values(response) -> list[str]:
    """Return every Access-Control-Allow-Origin value (httpx multi-header)."""
    get_list = getattr(response.headers, "get_list", None)
    if callable(get_list):
        return get_list("access-control-allow-origin")
    multi_items = getattr(response.headers, "multi_items", None)
    if callable(multi_items):
        return [v for k, v in multi_items() if k.lower() == "access-control-allow-origin"]
    raw = response.headers.get("access-control-allow-origin")
    return [raw] if raw else []


def _assert_single_acao(response, expected: str | None = "*") -> None:
    values = _acao_header_values(response)
    assert len(values) == 1, values
    assert "," not in values[0]
    if expected is not None:
        assert values[0] == expected


def test_app_get_and_options_emit_single_acao_wildcard(monkeypatch):
    from app.config import settings
    from app.main import app

    monkeypatch.setattr(settings, "cors_origins", "*")
    client = TestClient(app)
    origin = {"Origin": "https://test.1ink.us"}

    songs = client.get("/api/songs", headers=origin)
    assert songs.status_code == 200
    _assert_single_acao(songs)

    songs_preflight = client.options(
        "/api/songs",
        headers={
            **origin,
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert songs_preflight.status_code == 204
    _assert_single_acao(songs_preflight)

    tts = client.get("/models/tts/health", headers=origin)
    # /tts/health can 404 if it is captured by /{model_id}/{file_path}
    assert tts.status_code in (200, 404)
    _assert_single_acao(tts)

    tts_preflight = client.options(
        "/models/tts/health",
        headers={
            **origin,
            "Access-Control-Request-Method": "GET",
        },
    )
    assert tts_preflight.status_code == 204
    _assert_single_acao(tts_preflight)

    missing_file = client.get("/files/cors-acao-probe.txt", headers=origin)
    assert missing_file.status_code in (200, 404)
    _assert_single_acao(missing_file)


def test_acao_tokens_collapses_stacked_wildcard():
    from starlette.datastructures import MutableHeaders

    from app.main import _acao_tokens, _set_single_acao

    headers = MutableHeaders()
    headers.append("access-control-allow-origin", "*")
    headers.append("access-control-allow-origin", "*")
    tokens = _acao_tokens(headers)
    assert tokens == ["*", "*"]
    _set_single_acao(headers, tokens[0])
    assert headers.getlist("access-control-allow-origin") == ["*"]
    assert "," not in headers["access-control-allow-origin"]
