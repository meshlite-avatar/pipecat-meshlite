import pytest
from conftest import PK, SK
from pipecat.frames.frames import (
    ErrorFrame,
    InterruptionFrame,
    STTUpdateSettingsFrame,
    TTSAudioRawFrame,
    TTSUpdateSettingsFrame,
)
from pipecat.processors.frameworks.rtvi import (
    RTVIClientMessageFrame,
    RTVIServerMessageFrame,
    RTVIServerResponseFrame,
)
from pipecat.services.settings import STTSettings, TTSSettings
from pipecat.tests.utils import SleepFrame, run_test
from pipecat.transcriptions.language import Language

from pipecat_meshlite import MeshLiteAvatarProcessor, MeshLiteClient


@pytest.fixture(autouse=True)
async def close_clients():
    made.clear()
    yield
    for client in made:
        await client.close()


made: list[MeshLiteClient] = []


def make_processor(meshlite, **kwargs):
    client = MeshLiteClient(
        publishable_key=PK,
        secret_key=SK,
        origin="http://localhost:7860",
        avatar="av-1",
        api_url=meshlite.url,
    )
    made.append(client)
    return MeshLiteAvatarProcessor(client=client, **kwargs)


def request(msg_id="1", type_="meshlite-session"):
    return RTVIClientMessageFrame(msg_id=msg_id, type=type_, data={})


async def test_answers_the_session_request(meshlite):
    processor = make_processor(meshlite, language=Language.FR)
    created = []

    @processor.event_handler("on_session_created")
    async def on_created(_processor, session):
        created.append(session)

    down, _ = await run_test(
        processor,
        frames_to_send=[request(), SleepFrame(0.5)],
        expected_down_frames=[RTVIServerResponseFrame],
    )
    response = down[0]
    assert response.client_msg.msg_id == "1"
    assert response.error is None
    assert response.data == {
        "sessionToken": "tok-1",
        "publishableKey": PK,
        "avatar": "av-1",
        "language": "fr",
    }
    assert meshlite.requests[0]["body"]["conversation"] == {"language": "fr"}
    assert len(created) == 1
    assert processor.sessions_created == 1


async def test_bot_audio_passes_through_untouched(meshlite):
    audio = TTSAudioRawFrame(audio=b"\x01\x02" * 240, sample_rate=24000, num_channels=1)
    other = request(type_="something-else")
    down, _ = await run_test(
        make_processor(meshlite),
        # System frames overtake queued data frames: let the audio through first.
        frames_to_send=[audio, SleepFrame(0.1), InterruptionFrame(), other],
        expected_down_frames=[TTSAudioRawFrame, InterruptionFrame, RTVIClientMessageFrame],
    )
    assert down[0] is audio
    assert down[2] is other
    assert meshlite.requests == []


async def test_refusal_answers_with_an_error_and_a_non_fatal_error_frame(meshlite):
    meshlite.status = 403
    processor = make_processor(meshlite)
    errors = []

    @processor.event_handler("on_session_error")
    async def on_error(_processor, error):
        errors.append(error)

    down, up = await run_test(
        processor,
        frames_to_send=[request(), SleepFrame(0.5)],
        expected_down_frames=[RTVIServerResponseFrame],
        expected_up_frames=[ErrorFrame],
    )
    assert down[0].error == "meshlite_session_refused:ORIGIN_NOT_ALLOWED"
    assert up[0].fatal is False
    # A refused face must not end the call (ProcessorUnusablePolicy.END would).
    assert up[0].category.is_permanent is False
    assert processor.is_usable
    assert errors[0].code == "ORIGIN_NOT_ALLOWED"
    assert processor.sessions_created == 0


async def test_limits_sessions_per_connection(meshlite):
    down, _ = await run_test(
        make_processor(meshlite, max_sessions=1),
        frames_to_send=[request("1"), SleepFrame(0.5), request("2"), SleepFrame(0.2)],
        expected_down_frames=[RTVIServerResponseFrame, RTVIServerResponseFrame],
    )
    assert down[0].error is None
    assert down[1].error == "meshlite_session_limit"
    assert len(meshlite.requests) == 1


async def test_tells_the_page_when_the_language_changes(meshlite):
    to_french = TTSUpdateSettingsFrame(delta=TTSSettings(language=Language.FR_FR))
    still_french = STTUpdateSettingsFrame(delta=STTSettings(language=Language.FR))
    to_spanish = STTUpdateSettingsFrame(settings={"language": "es"})
    voice_only = TTSUpdateSettingsFrame(delta=TTSSettings(voice="alice"))
    down, _ = await run_test(
        make_processor(meshlite, language="en"),
        frames_to_send=[to_french, still_french, to_spanish, voice_only],
        expected_down_frames=[
            RTVIServerMessageFrame,
            TTSUpdateSettingsFrame,
            STTUpdateSettingsFrame,
            RTVIServerMessageFrame,
            STTUpdateSettingsFrame,
            TTSUpdateSettingsFrame,
        ],
    )
    assert down[0].data == {"type": "meshlite-language", "language": "fr"}
    assert down[3].data == {"type": "meshlite-language", "language": "auto"}
    assert down[1] is to_french


async def test_language_following_can_be_turned_off(meshlite):
    await run_test(
        make_processor(meshlite, follow_language=False),
        frames_to_send=[TTSUpdateSettingsFrame(delta=TTSSettings(language=Language.FR))],
        expected_down_frames=[TTSUpdateSettingsFrame],
    )


def test_rejects_a_bad_session_limit(meshlite):
    with pytest.raises(ValueError, match="max_sessions"):
        make_processor(meshlite, max_sessions=0)


def test_needs_keys_without_a_client():
    with pytest.raises(ValueError, match="MESHLITE_PUBLISHABLE_KEY"):
        MeshLiteAvatarProcessor()


@pytest.mark.parametrize("status", [401, 403, 400])
async def test_refusal_never_makes_the_processor_unusable(meshlite, status):
    meshlite.status = status
    processor = make_processor(meshlite)
    audio = TTSAudioRawFrame(audio=b"\x00\x01" * 240, sample_rate=24000, num_channels=1)
    down, up = await run_test(
        processor,
        frames_to_send=[request(), SleepFrame(0.5), audio],
        expected_down_frames=[RTVIServerResponseFrame, TTSAudioRawFrame],
        expected_up_frames=[ErrorFrame],
    )
    assert processor.is_usable
    assert down[1] is audio
