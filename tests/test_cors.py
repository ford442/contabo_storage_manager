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
            r"^https://([a-z0-9-]+\.)?(1ink\.us|noahcohn\.com)$|^http://(localhost|127\.0\.0\.1)(:\d+)?$",
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
