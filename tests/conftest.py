import pytest
from aiohttp import web

PK = "ml_pk_test_abc123"
SK = "ml_sk_test_secret456"


class FakeMeshLite:
    """A local stand-in for POST /api/v1/client-sessions."""

    def __init__(self):
        self.requests: list[dict] = []
        self.status = 200
        self.reply: dict | str | None = None

    async def handle(self, request: web.Request) -> web.StreamResponse:
        body = await request.json()
        self.requests.append({"headers": dict(request.headers), "body": body})
        if self.status >= 300:
            payload = (
                self.reply
                if self.reply is not None
                # The shape MeshLite uses (seen on console.meshlite.io, 21 Nov 2026).
                else {"error": {"code": "ORIGIN_NOT_ALLOWED", "message": "...", "retryable": False}}
            )
            if isinstance(payload, str):
                return web.Response(status=self.status, text=payload)
            return web.json_response(payload, status=self.status)
        return web.json_response({"sessionToken": f"tok-{len(self.requests)}"})


@pytest.fixture
async def meshlite(aiohttp_server):
    fake = FakeMeshLite()
    app = web.Application()
    app.router.add_post("/api/v1/client-sessions", fake.handle)
    server = await aiohttp_server(app)
    fake.url = str(server.make_url("")).rstrip("/")
    return fake


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in (
        "MESHLITE_PUBLISHABLE_KEY",
        "MESHLITE_SECRET_KEY",
        "MESHLITE_ORIGIN",
        "MESHLITE_AVATAR",
        "MESHLITE_API",
    ):
        monkeypatch.delenv(name, raising=False)
