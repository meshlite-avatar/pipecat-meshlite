"""Pipeline processor that links a Pipecat bot to the MeshLite face in its client page.

The face is drawn in the visitor's browser by the MeshLite web SDK, from the audio track
the page already receives from the bot (``avatar.attachPipecat(client)``). The bot's
audio is never changed, delayed or duplicated: this processor only answers RTVI
messages, so the bot keeps its own speech-to-text, LLM, voice and transport.
"""

from __future__ import annotations

from typing import Any

from loguru import logger
from pipecat.frames.frames import (
    ErrorCategory,
    Frame,
    InterruptionFrame,
    LLMFullResponseStartFrame,
    TTSTextFrame,
    TTSUpdateSettingsFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.processors.frameworks.rtvi import (
    RTVIClientMessageFrame,
    RTVIServerMessageFrame,
    RTVIServerResponseFrame,
)
from pipecat.services.settings import is_given
from pipecat.transcriptions.language import Language

from pipecat_meshlite.client import MeshLiteClient, MeshLiteError
from pipecat_meshlite.languages import spoken_language_of, to_meshlite_language

SESSION_REQUEST = "meshlite-session"
"""RTVI client request type the page sends to get its MeshLite session."""

LANGUAGE_MESSAGE = "meshlite-language"
"""RTVI server message type sent when the bot's language changes."""


class MeshLiteAvatarProcessor(FrameProcessor):
    """Gives the bot's client page its MeshLite session and keeps its lips in the right language.

    Place it after the TTS service, for example right before ``transport.output()``: it
    reads the text the TTS pushes. Every frame passes through unchanged, except the page's
    ``meshlite-session`` requests, which it answers.

    1. **Session over RTVI.** The page asks with
       ``client.sendClientRequest("meshlite-session", {})``. The processor creates a
       MeshLite client session (``audio: "track"``) with the server-side secret key and
       answers with ``{sessionToken, publishableKey, avatar, language}``. The page needs no
       endpoint of its own, and a session exists only for a connected bot. At most
       ``max_sessions`` requests are sent to MeshLite per connection, refusals included
       (the SDK asks again only to renew a session or after a temporary failure).
    2. **Lip language.** Like the MeshLite web SDK with its own voice, it reads the words
       the bot speaks (``TTSTextFrame``, pushed by the TTS service) and, as soon as the
       current reply is clearly in another language (English, French or Mandarin), sends
       the RTVI server message ``{"type": "meshlite-language", "language": "fr"}``; the page
       calls ``avatar.setLanguage(language)`` and the lip model of that language takes over
       between two replies. A bot that switches on purpose can also call
       :meth:`set_language`. The lips follow the bot's voice, never the visitor's
       speech-to-text. A ``TTSUpdateSettingsFrame`` is followed only when the processor
       sits before the TTS service, which consumes it.

    If MeshLite refuses a session, the page gets an error response
    (``meshlite_session_refused:<code>``, final, or ``meshlite_session_unavailable:<code>``
    for a network error, 429 or 5xx, worth another try) and the processor
    pushes a non-fatal, non-permanent ``ErrorFrame``: the processor stays usable, the bot
    keeps talking and the SDK plays its voice without the face.

    Event handlers:

    - ``on_session_created(processor, session)``: a session was created for the page.
    - ``on_session_error(processor, error)``: MeshLite refused or could not be reached
      (:class:`~pipecat_meshlite.client.MeshLiteError`).

    Example::

        avatar = MeshLiteAvatarProcessor(language=Language.EN)
        pipeline = Pipeline([transport.input(), stt, user_agg, llm, tts, avatar,
                             transport.output(), assistant_agg])
    """

    def __init__(
        self,
        *,
        client: MeshLiteClient | None = None,
        avatar: str | None = None,
        language: Language | str | None = None,
        max_sessions: int = 3,
        follow_language: bool = True,
        **kwargs: Any,
    ):
        """Initialize the processor.

        Args:
            client: The MeshLite client that creates sessions. Defaults to a
                :class:`~pipecat_meshlite.client.MeshLiteClient` configured from the
                ``MESHLITE_*`` environment variables; the processor then closes it.
            avatar: Avatar id. Defaults to the client's avatar.
            language: The language the bot speaks at the start. It picks the lip model
                of each new session.
            max_sessions: Most session requests sent to MeshLite for one connection,
                refusals included (1 to 10).
            follow_language: Whether to tell the page when the bot's language changes.
            **kwargs: Passed to :class:`~pipecat.processors.frame_processor.FrameProcessor`.

        Raises:
            ValueError: If ``max_sessions`` is out of range, or if ``client`` is omitted
                and the ``MESHLITE_*`` environment is incomplete.
        """
        super().__init__(**kwargs)
        if not 1 <= max_sessions <= 10:
            raise ValueError("max_sessions must be between 1 and 10.")
        self._owns_client = client is None
        self._client = client or MeshLiteClient()
        self._avatar = avatar
        self._language = to_meshlite_language(language)
        self._max_sessions = max_sessions
        self._follow_language = follow_language
        self._sessions_created = 0
        self._attempts = 0
        self._reply = ""
        self._tasks: set = set()
        self._register_event_handler("on_session_created")
        self._register_event_handler("on_session_error")

    @property
    def language(self) -> str | None:
        """Current MeshLite lip-model code (``"en"``, ``"fr"``, ``"zh-CN"``, ``"auto"``)."""
        return self._language

    @property
    def sessions_created(self) -> int:
        """How many sessions this processor created for its connection."""
        return self._sessions_created

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        """Answer MeshLite requests and pass every other frame through.

        Args:
            frame: The frame to process.
            direction: The direction of the frame.
        """
        await super().process_frame(frame, direction)

        if isinstance(frame, RTVIClientMessageFrame) and frame.type == SESSION_REQUEST:
            # Answer in a task: the MeshLite request must not hold up the bot's frames.
            task = self.create_task(self._answer_session_request(frame), "meshlite_session")
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
            return

        if self._follow_language:
            if isinstance(frame, (LLMFullResponseStartFrame, InterruptionFrame)):
                self._reply = ""
            elif isinstance(frame, TTSTextFrame) and direction == FrameDirection.DOWNSTREAM:
                self._reply = f"{self._reply} {frame.text}".strip()[-2000:]
                spoken = spoken_language_of(self._reply)
                if spoken is not None:
                    await self.set_language(spoken)
            elif isinstance(frame, TTSUpdateSettingsFrame):
                await self._follow(frame)

        await self.push_frame(frame, direction)

    async def cleanup(self):
        """Cancel pending session requests and close the MeshLite client if owned."""
        await super().cleanup()
        for task in list(self._tasks):
            await self.cancel_task(task)
        self._tasks.clear()
        if self._owns_client:
            await self._client.close()

    async def _answer_session_request(self, request: RTVIClientMessageFrame):
        if self._attempts >= self._max_sessions:
            logger.warning(f"{self}: MeshLite session limit reached ({self._max_sessions}).")
            await self.push_frame(
                RTVIServerResponseFrame(client_msg=request, error="meshlite_session_limit")
            )
            return

        # Refusals count too: a page that keeps asking, or a wrong setting such as
        # ORIGIN_NOT_ALLOWED, cannot send MeshLite more than max_sessions requests.
        self._attempts += 1
        try:
            session = await self._client.create_session(
                avatar=self._avatar, language=self._language
            )
        except MeshLiteError as error:
            kind = "unavailable" if error.retryable else "refused"
            await self.push_frame(
                RTVIServerResponseFrame(
                    client_msg=request, error=f"meshlite_session_{kind}:{error.code}"
                )
            )
            await self._call_event_handler("on_session_error", error)
            await self.push_error(
                f"{self} MeshLite session refused: {error.code}",
                exception=error,
                category=self._category(error),
            )
            return

        self._sessions_created += 1
        await self.push_frame(RTVIServerResponseFrame(client_msg=request, data=session.to_client()))
        await self._call_event_handler("on_session_created", session)

    async def set_language(self, language: Language | str) -> None:
        """Tell the page that the bot now speaks ``language``, so the lips follow.

        Nothing is sent when the lip model does not change.

        Args:
            language: A Pipecat language or a BCP-47 code such as ``"fr-FR"``.
        """
        meshlite_language = to_meshlite_language(language)
        if meshlite_language is None or meshlite_language == self._language:
            return
        self._language = meshlite_language
        await self.push_frame(
            RTVIServerMessageFrame(data={"type": LANGUAGE_MESSAGE, "language": meshlite_language})
        )

    async def _follow(self, frame: TTSUpdateSettingsFrame):
        language = None
        if frame.delta is not None and is_given(frame.delta.language):
            language = frame.delta.language
        elif "language" in frame.settings:
            language = frame.settings["language"]
        if language is not None:
            await self.set_language(language)

    @staticmethod
    def _category(error: MeshLiteError) -> ErrorCategory:
        # Never a permanent category (authentication, authorization, invalid request):
        # Pipecat would mark this processor unusable, and a worker with
        # ProcessorUnusablePolicy.END would end the call. A refused face must not end
        # the conversation; the page keeps playing the bot's voice.
        if error.status is None:
            return ErrorCategory.CONNECTIVITY
        if error.status == 429:
            return ErrorCategory.RATE_LIMIT
        if error.status >= 500:
            return ErrorCategory.SERVER
        if error.status == 402:
            return ErrorCategory.QUOTA
        return ErrorCategory.APPLICATION
