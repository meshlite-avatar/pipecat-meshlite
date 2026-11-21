"""MeshLite avatar face for Pipecat bots.

The face is lip-synced in the visitor's browser by the MeshLite web SDK, from the audio
track the page already receives from the bot. This package holds the server side:

- :class:`MeshLiteClient` creates MeshLite client sessions (``audio: "track"``) with your
  secret key, for any web route.
- :class:`MeshLiteAvatarProcessor` answers the page's ``meshlite-session`` RTVI request
  and tells the page when the bot's language changes, without touching the bot's audio.
"""

from pipecat_meshlite.client import MeshLiteClient, MeshLiteError, MeshLiteSession
from pipecat_meshlite.languages import MESHLITE_LANGUAGES, to_meshlite_language
from pipecat_meshlite.processor import LANGUAGE_MESSAGE, SESSION_REQUEST, MeshLiteAvatarProcessor
from pipecat_meshlite.version import __version__

__all__ = [
    "LANGUAGE_MESSAGE",
    "MESHLITE_LANGUAGES",
    "SESSION_REQUEST",
    "MeshLiteAvatarProcessor",
    "MeshLiteClient",
    "MeshLiteError",
    "MeshLiteSession",
    "__version__",
    "to_meshlite_language",
]
