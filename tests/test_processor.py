import pytest
from conftest import PK, SK
from pipecat.frames.frames import (
    ErrorCategory,
    ErrorFrame,
    InterruptionFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    STTUpdateSettingsFrame,
    TTSAudioRawFrame,
    TTSUpdateSettingsFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.processors.frameworks.rtvi import (
    RTVIClientMessageFrame,
    RTVIServerMessageFrame,
    RTVIServerResponseFrame,
)
from pipecat.services.settings import STTSettings, TTSSettings
from pipecat.services.tts_service import TTSService
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


async def test_refusals_count_toward_the_session_limit(meshlite):
    meshlite.status = 403
    down, _ = await run_test(
        make_processor(meshlite, max_sessions=2),
        frames_to_send=[request("1"), SleepFrame(0.3), request("2"), SleepFrame(0.3), request("3")]
        + [SleepFrame(0.2)],
        expected_down_frames=[RTVIServerResponseFrame] * 3,
        ignore_start=True,
    )
    assert [frame.error for frame in down] == [
        "meshlite_session_refused:ORIGIN_NOT_ALLOWED",
        "meshlite_session_refused:ORIGIN_NOT_ALLOWED",
        "meshlite_session_limit",
    ]
    assert len(meshlite.requests) == 2


@pytest.mark.parametrize(("status", "kind"), [(429, "unavailable"), (503, "unavailable")])
async def test_temporary_failures_tell_the_page_to_try_again(meshlite, status, kind):
    meshlite.status = status
    down, _ = await run_test(
        make_processor(meshlite),
        frames_to_send=[request(), SleepFrame(0.5)],
        expected_down_frames=[RTVIServerResponseFrame],
        expected_up_frames=[ErrorFrame],
    )
    assert down[0].error.startswith(f"meshlite_session_{kind}:")


async def test_limits_sessions_per_connection(meshlite):
    down, _ = await run_test(
        make_processor(meshlite, max_sessions=1),
        frames_to_send=[request("1"), SleepFrame(0.5), request("2"), SleepFrame(0.2)],
        expected_down_frames=[RTVIServerResponseFrame, RTVIServerResponseFrame],
    )
    assert down[0].error is None
    assert down[1].error == "meshlite_session_limit"
    assert len(meshlite.requests) == 1


async def test_follows_a_voice_settings_frame_when_placed_before_the_tts(meshlite):
    to_french = TTSUpdateSettingsFrame(delta=TTSSettings(language=Language.FR_FR))
    still_french = TTSUpdateSettingsFrame(settings={"language": "fr"})
    to_spanish = TTSUpdateSettingsFrame(settings={"language": "es"})
    voice_only = TTSUpdateSettingsFrame(delta=TTSSettings(voice="alice"))
    down, _ = await run_test(
        make_processor(meshlite, language="en"),
        frames_to_send=[to_french, still_french, to_spanish, voice_only],
        expected_down_frames=[
            RTVIServerMessageFrame,
            TTSUpdateSettingsFrame,
            TTSUpdateSettingsFrame,
            RTVIServerMessageFrame,
            TTSUpdateSettingsFrame,
            TTSUpdateSettingsFrame,
        ],
    )
    assert down[0].data == {"type": "meshlite-language", "language": "fr"}
    assert down[3].data == {"type": "meshlite-language", "language": "auto"}
    assert down[1] is to_french


async def test_the_visitors_speech_to_text_language_is_not_followed(meshlite):
    # The lips follow the bot's voice, not what the visitor's STT is set to.
    await run_test(
        make_processor(meshlite, language="en"),
        frames_to_send=[STTUpdateSettingsFrame(delta=STTSettings(language=Language.FR))],
        expected_down_frames=[STTUpdateSettingsFrame],
    )


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


class _SilentTTS(TTSService):
    """A real TTSService: it consumes settings frames and pushes TTSTextFrame."""

    def __init__(self):
        super().__init__(
            sample_rate=16000, settings=TTSSettings(model=None, voice=None, language=None)
        )

    def can_generate_metrics(self):
        return False

    async def run_tts(self, text, context_id):
        yield TTSAudioRawFrame(
            audio=b"\0\0" * 160, sample_rate=16000, num_channels=1, context_id=context_id
        )


def reply(*sentences):
    return [
        LLMFullResponseStartFrame(),
        *(LLMTextFrame(sentence) for sentence in sentences),
        LLMFullResponseEndFrame(),
        SleepFrame(0.2),
    ]


def languages_sent(frames):
    return [
        frame.data["language"]
        for frame in frames
        if isinstance(frame, RTVIServerMessageFrame)
        and frame.data.get("type") == "meshlite-language"
    ]


async def test_lips_follow_the_language_the_bot_speaks_behind_a_real_tts(meshlite):
    processor = make_processor(meshlite, language=Language.EN)
    down, _ = await run_test(
        Pipeline([_SilentTTS(), processor]),
        frames_to_send=[
            *reply("Hello, I am happy to help you with this question. "),
            *reply("Bonjour, je suis très content de vous aider. ", "C'est une bonne question."),
            *reply("Merci, et bonne journée à vous. "),
            *reply("你好，我很高兴帮助你回答这个问题。"),
            *reply("Thanks, it is very well done and I can help you. "),
        ],
    )
    assert languages_sent(down) == ["fr", "zh-CN", "en"]
    assert processor.language == "en"


async def test_short_or_mixed_replies_keep_the_lips(meshlite):
    processor = make_processor(meshlite, language=Language.FR)
    down, _ = await run_test(
        Pipeline([_SilentTTS(), processor]),
        frames_to_send=[*reply("OK. "), *reply("Hola, ¿cómo estás hoy? "), *reply("Yes, oui. ")],
    )
    assert languages_sent(down) == []


async def test_settings_frame_is_swallowed_but_set_language_works(meshlite):
    processor = make_processor(meshlite, language=Language.EN)
    tts = _SilentTTS()
    down, _ = await run_test(
        Pipeline([tts, processor]),
        frames_to_send=[
            TTSUpdateSettingsFrame(delta=TTSSettings(language=Language.FR)),
            SleepFrame(0.1),
        ],
    )
    assert languages_sent(down) == []
    sent = []

    async def capture(frame, direction=None):
        sent.append(frame)

    processor.push_frame = capture
    await processor.set_language("fr-FR")
    await processor.set_language(Language.FR)
    assert languages_sent(sent) == ["fr"]


async def test_a_402_is_a_quota_error(meshlite):
    meshlite.status = 402
    _, up = await run_test(
        make_processor(meshlite),
        frames_to_send=[request(), SleepFrame(0.5)],
        expected_down_frames=[RTVIServerResponseFrame],
        expected_up_frames=[ErrorFrame],
    )
    assert up[0].category is ErrorCategory.QUOTA
