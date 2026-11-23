"""Server-side client for MeshLite client sessions.

A MeshLite face in the browser needs a short-lived session token. Only your server may
create it, because the request is signed with your MeshLite secret key. The token is
created with ``audio: "track"``: the face then follows the audio track your page already
receives from the Pipecat bot, and MeshLite counts no voice minutes.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any

import aiohttp
from loguru import logger
from pipecat.transcriptions.language import Language

from pipecat_meshlite.languages import to_meshlite_language
from pipecat_meshlite.version import __version__

DEFAULT_API_URL = "https://console.meshlite.io"
"""Production MeshLite API. Set ``MESHLITE_API`` or ``api_url`` for another environment."""

SESSION_PATH = "/api/v1/client-sessions"

_KEY = re.compile(r"^ml_(pk|sk)_(test|live)_[A-Za-z0-9_-]+$")
_ORIGIN = re.compile(r"^https?://[^/\s?#]+$")


class MeshLiteError(Exception):
    """A MeshLite session could not be created.

    Parameters:
        code: MeshLite error code (for example ``ORIGIN_NOT_ALLOWED``), ``NETWORK`` for a
            connection problem, or the HTTP status as text when MeshLite gave no code.
        status: HTTP status of the MeshLite response, or ``None`` when no response came.
        retryable: Whether trying again later can succeed (network, 429 or 5xx).
    """

    def __init__(self, message: str, *, code: str, status: int | None = None, retryable: bool):
        super().__init__(message)
        self.code = code
        self.status = status
        self.retryable = retryable


@dataclass(frozen=True)
class MeshLiteSession:
    """A MeshLite client session for one visitor.

    Parameters:
        session_token: Signed, short-lived token for ``MeshLite.mount({ sessionToken })``.
            It is safe to send to the browser; it never contains the secret key.
        publishable_key: The publishable key the session was created for.
        avatar: The avatar id, or ``None`` for the plan's default avatar.
        language: MeshLite lip-model code of the session (``"en"``, ``"fr"``,
            ``"zh-CN"`` or ``"auto"``), or ``None`` when no language was set.
    """

    session_token: str = field(repr=False)
    publishable_key: str
    avatar: str | None = None
    language: str | None = None

    def to_client(self) -> dict[str, Any]:
        """Return the fields the browser needs, in the MeshLite web SDK's naming.

        Returns:
            ``{"sessionToken", "publishableKey", "avatar", "language"}``.
        """
        return {
            "sessionToken": self.session_token,
            "publishableKey": self.publishable_key,
            "avatar": self.avatar,
            "language": self.language,
        }


def _key_environment(key: str, kind: str, name: str) -> str:
    match = _KEY.match(key)
    if not match or match.group(1) != kind:
        # Never echo the value: it may be a secret pasted in the wrong place.
        raise ValueError(
            f"{name} is not a MeshLite {'publishable' if kind == 'pk' else 'secret'} key."
        )
    return match.group(2)


def _sends_key_in_clear(api_url: str) -> bool:
    if not api_url.lower().startswith("http://"):
        return False
    host = api_url[len("http://") :].split("/", 1)[0].rsplit(":", 1)[0].strip("[]").lower()
    return host not in {"localhost", "127.0.0.1", "::1"} and not host.endswith(".localhost")


def _error_code(payload: Any) -> str | None:
    """Return MeshLite's error code: ``{"error": {"code": ...}}`` or ``{"code": ...}``."""
    if not isinstance(payload, dict):
        return None
    error = payload.get("error")
    if isinstance(error, dict) and isinstance(error.get("code"), str):
        return error["code"]
    for value in (payload.get("code"), error):
        if isinstance(value, str) and value:
            return value
    return None


def normalize_origin(origin: str) -> str:
    """Return ``origin`` as ``scheme://host[:port]``, without a trailing slash.

    Args:
        origin: The address of the page that shows the avatar, for example
            ``https://www.example.com`` or ``http://localhost:7860``.

    Returns:
        The normalized origin.

    Raises:
        ValueError: If ``origin`` is not an http(s) origin without a path.
    """
    value = origin.strip().rstrip("/")
    if not _ORIGIN.match(value):
        raise ValueError(
            f"origin must look like https://www.example.com (no path), got {origin!r}."
        )
    return value


class MeshLiteClient:
    """Creates MeshLite client sessions with ``audio: "track"`` for Pipecat pages.

    Keys and settings default to environment variables, so a bot needs no code change
    between test and live keys:

    - ``MESHLITE_PUBLISHABLE_KEY`` and ``MESHLITE_SECRET_KEY``: a key pair of the same
      environment (both ``test`` or both ``live``), from the MeshLite Console.
    - ``MESHLITE_ORIGIN``: the address of your page. It must be declared as an allowed
      site of the project. Set it on the server, never from what the browser says.
    - ``MESHLITE_AVATAR`` (optional): the avatar id.
    - ``MESHLITE_API`` (optional): another MeshLite environment.

    Example::

        client = MeshLiteClient()
        session = await client.create_session(language="en")
        return session.to_client()   # send this to the page
    """

    def __init__(
        self,
        *,
        publishable_key: str | None = None,
        secret_key: str | None = None,
        origin: str | None = None,
        avatar: str | None = None,
        api_url: str | None = None,
        aiohttp_session: aiohttp.ClientSession | None = None,
        timeout: float = 8.0,
    ):
        """Initialize the client.

        Args:
            publishable_key: MeshLite publishable key (``ml_pk_test_...`` or
                ``ml_pk_live_...``). Defaults to ``MESHLITE_PUBLISHABLE_KEY``.
            secret_key: MeshLite secret key of the same environment. Defaults to
                ``MESHLITE_SECRET_KEY``. It never leaves this server and is never logged.
            origin: Address of the page that shows the avatar. Defaults to
                ``MESHLITE_ORIGIN``.
            avatar: Avatar id. Defaults to ``MESHLITE_AVATAR``; without one, the session
                gets the plan's default avatar.
            api_url: MeshLite API base URL. Defaults to ``MESHLITE_API``, then
                ``https://console.meshlite.io``.
            aiohttp_session: Optional shared HTTP session. When omitted, the client opens
                its own and closes it in :meth:`close`.
            timeout: Timeout, in seconds, of one session request. The default stays
                well under the web SDK's 15 s wait for a session token.

        Raises:
            ValueError: If a key is missing or malformed, if the two keys belong to
                different environments, or if the origin is not valid.
        """
        publishable_key = publishable_key or os.environ.get("MESHLITE_PUBLISHABLE_KEY", "")
        secret_key = secret_key or os.environ.get("MESHLITE_SECRET_KEY", "")
        origin = origin or os.environ.get("MESHLITE_ORIGIN", "")
        if not publishable_key or not secret_key:
            raise ValueError(
                "Set MESHLITE_PUBLISHABLE_KEY and MESHLITE_SECRET_KEY (MeshLite Console, Keys)."
            )
        if not origin:
            raise ValueError(
                "Set MESHLITE_ORIGIN to the address of the page that shows the avatar."
            )
        environment = _key_environment(publishable_key.strip(), "pk", "MESHLITE_PUBLISHABLE_KEY")
        if _key_environment(secret_key.strip(), "sk", "MESHLITE_SECRET_KEY") != environment:
            raise ValueError(
                "The publishable key and the secret key must both be test keys or both live keys."
            )

        self._publishable_key = publishable_key.strip()
        self._secret_key = secret_key.strip()
        self._origin = normalize_origin(origin)
        self._avatar = (avatar or os.environ.get("MESHLITE_AVATAR", "")).strip() or None
        self._api_url = (api_url or os.environ.get("MESHLITE_API") or DEFAULT_API_URL).rstrip("/")
        if _sends_key_in_clear(self._api_url):
            logger.warning(
                "MeshLite API URL uses http:// outside localhost: the secret key would travel "
                "unencrypted. Use https://."
            )
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._session = aiohttp_session
        self._owns_session = aiohttp_session is None
        self.environment = environment
        """``"test"`` or ``"live"``, from the keys."""

    def __repr__(self) -> str:
        return (
            f"MeshLiteClient(environment={self.environment!r}, origin={self._origin!r}, "
            f"api_url={self._api_url!r})"
        )

    @property
    def origin(self) -> str:
        """The page origin sessions are created for."""
        return self._origin

    @property
    def avatar(self) -> str | None:
        """The default avatar id, or ``None`` for the plan's default avatar."""
        return self._avatar

    async def create_session(
        self,
        *,
        avatar: str | None = None,
        language: Language | str | None = None,
        origin: str | None = None,
        end_user_token: str | None = None,
    ) -> MeshLiteSession:
        """Create one MeshLite client session with ``audio: "track"``.

        Each call creates a new session, which counts as one conversation of the
        MeshLite plan (concurrent conversations, statistics). A session lasts at most
        two hours.

        Args:
            avatar: Avatar id for this session. Defaults to the client's avatar.
            language: The language the bot speaks; it picks the face's lip model
                (see :func:`~pipecat_meshlite.languages.to_meshlite_language`).
            origin: Page origin for this session. Defaults to the client's origin.
            end_user_token: Optional signed JWT that identifies the end user, for
                per-seat plans.

        Returns:
            The new session.

        Raises:
            MeshLiteError: If MeshLite refused the request or could not be reached.
        """
        avatar = avatar or self._avatar
        meshlite_language = to_meshlite_language(language)
        body: dict[str, Any] = {
            "publishableKey": self._publishable_key,
            "origin": normalize_origin(origin) if origin else self._origin,
            "audio": "track",
        }
        if avatar:
            body["avatar"] = avatar
        if meshlite_language:
            body["conversation"] = {"language": meshlite_language}
        if end_user_token:
            body["endUserToken"] = end_user_token

        headers = {
            "Authorization": f"Bearer {self._secret_key}",
            "User-Agent": f"pipecat-meshlite/{__version__}",
        }
        url = f"{self._api_url}{SESSION_PATH}"
        try:
            http = await self._http()
            async with http.post(
                url, json=body, headers=headers, timeout=self._timeout
            ) as response:
                status = response.status
                try:
                    payload = await response.json(content_type=None)
                except ValueError:
                    payload = None
        except (aiohttp.ClientError, TimeoutError) as error:
            raise MeshLiteError(
                f"MeshLite could not be reached ({type(error).__name__}).",
                code="NETWORK",
                retryable=True,
            ) from error

        token = payload.get("sessionToken") if isinstance(payload, dict) else None
        if status >= 300 or not isinstance(token, str) or not token:
            code = str(_error_code(payload) or status)
            logger.warning(f"MeshLite refused the session: {code} (HTTP {status})")
            raise MeshLiteError(
                f"MeshLite refused the session: {code} (HTTP {status}).",
                code=code,
                status=status,
                retryable=status == 429 or status >= 500,
            )
        return MeshLiteSession(
            session_token=token,
            publishable_key=self._publishable_key,
            avatar=avatar,
            language=meshlite_language,
        )

    async def close(self) -> None:
        """Close the HTTP session if this client opened it. Safe to call twice."""
        if self._owns_session and self._session is not None:
            session, self._session = self._session, None
            await session.close()

    async def __aenter__(self) -> MeshLiteClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()

    async def _http(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
            self._owns_session = True
        return self._session
