import pytest
from conftest import PK, SK
from pipecat.transcriptions.language import Language

from pipecat_meshlite import MeshLiteClient, MeshLiteError


def make(meshlite, **kwargs):
    options = dict(
        publishable_key=PK, secret_key=SK, origin="http://localhost:7860", api_url=meshlite.url
    )
    options.update(kwargs)
    return MeshLiteClient(**options)


async def test_creates_a_track_session(meshlite):
    async with make(meshlite, avatar="av-1") as client:
        session = await client.create_session(language=Language.EN_US)

    assert session.session_token == "tok-1"
    assert session.to_client() == {
        "sessionToken": "tok-1",
        "publishableKey": PK,
        "avatar": "av-1",
        "language": "en",
    }
    sent = meshlite.requests[0]
    assert sent["headers"]["Authorization"] == f"Bearer {SK}"
    assert sent["headers"]["User-Agent"].startswith("pipecat-meshlite/")
    assert sent["body"] == {
        "publishableKey": PK,
        "origin": "http://localhost:7860",
        "audio": "track",
        "avatar": "av-1",
        "conversation": {"language": "en"},
    }


async def test_omits_optional_fields(meshlite):
    async with make(meshlite) as client:
        await client.create_session()
    assert meshlite.requests[0]["body"] == {
        "publishableKey": PK,
        "origin": "http://localhost:7860",
        "audio": "track",
    }


async def test_reads_the_environment(meshlite, monkeypatch):
    monkeypatch.setenv("MESHLITE_PUBLISHABLE_KEY", PK)
    monkeypatch.setenv("MESHLITE_SECRET_KEY", SK)
    monkeypatch.setenv("MESHLITE_ORIGIN", "https://www.example.com/")
    monkeypatch.setenv("MESHLITE_AVATAR", "av-env")
    monkeypatch.setenv("MESHLITE_API", meshlite.url)
    async with MeshLiteClient() as client:
        assert client.environment == "test"
        assert client.origin == "https://www.example.com"
        session = await client.create_session(language="zh")
    assert session.avatar == "av-env"
    assert meshlite.requests[0]["body"]["conversation"] == {"language": "zh-CN"}


async def test_refusal_carries_the_code(meshlite):
    meshlite.status = 403
    async with make(meshlite) as client:
        with pytest.raises(MeshLiteError) as caught:
            await client.create_session()
    assert caught.value.code == "ORIGIN_NOT_ALLOWED"
    assert caught.value.status == 403
    assert caught.value.retryable is False


async def test_server_error_without_json_is_retryable(meshlite):
    meshlite.status = 503
    meshlite.reply = "<html>bad gateway</html>"
    async with make(meshlite) as client:
        with pytest.raises(MeshLiteError) as caught:
            await client.create_session()
    assert caught.value.code == "503"
    assert caught.value.retryable is True


async def test_network_error():
    client = MeshLiteClient(
        publishable_key=PK,
        secret_key=SK,
        origin="http://localhost:7860",
        api_url="http://127.0.0.1:9",
        timeout=2,
    )
    with pytest.raises(MeshLiteError) as caught:
        await client.create_session()
    await client.close()
    assert caught.value.code == "NETWORK"
    assert caught.value.retryable is True


@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({"publishable_key": ""}, "Set MESHLITE_PUBLISHABLE_KEY"),
        ({"origin": ""}, "Set MESHLITE_ORIGIN"),
        ({"publishable_key": SK}, "not a MeshLite publishable key"),
        ({"secret_key": PK}, "not a MeshLite secret key"),
        ({"secret_key": "ml_sk_live_xyz"}, "both be test keys or both live keys"),
        ({"origin": "https://example.com/page"}, "no path"),
    ],
)
def test_rejects_bad_configuration(kwargs, message):
    options = dict(publishable_key=PK, secret_key=SK, origin="http://localhost:7860")
    options.update(kwargs)
    with pytest.raises(ValueError, match=message) as caught:
        MeshLiteClient(**options)
    assert SK not in str(caught.value)


def test_never_shows_secrets():
    client = MeshLiteClient(publishable_key=PK, secret_key=SK, origin="http://localhost:7860")
    assert SK not in repr(client)


async def test_session_repr_hides_the_token(meshlite):
    async with make(meshlite) as client:
        session = await client.create_session()
    assert "tok-1" not in repr(session)


async def test_reads_a_flat_error_code(meshlite):
    meshlite.status = 409
    meshlite.reply = {"code": "SDK_UPGRADE_REQUIRED"}
    async with make(meshlite) as client:
        with pytest.raises(MeshLiteError) as caught:
            await client.create_session()
    assert caught.value.code == "SDK_UPGRADE_REQUIRED"


@pytest.mark.parametrize(
    ("api_url", "warns"),
    [
        ("http://api.example.com", True),
        ("http://localhost:8080", False),
        ("http://127.0.0.1:8080/", False),
        ("https://console.meshlite.io", False),
    ],
)
def test_warns_when_the_secret_key_would_travel_in_clear(api_url, warns):
    from loguru import logger

    messages = []
    sink = logger.add(lambda message: messages.append(str(message)), level="WARNING")
    try:
        MeshLiteClient(
            publishable_key=PK, secret_key=SK, origin="http://localhost", api_url=api_url
        )
    finally:
        logger.remove(sink)
    assert any("http://" in message for message in messages) is warns
