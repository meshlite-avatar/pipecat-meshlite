"""Map Pipecat languages to the lip models of the MeshLite web SDK.

The face's lips are computed in the visitor's browser by a small speech model trained
for one language. The SDK has one model each for English, French and Mandarin Chinese;
any other language keeps the model of the avatar's own language (``"auto"``).
"""

from __future__ import annotations

import re
import unicodedata

from pipecat.transcriptions.language import Language

MESHLITE_LANGUAGES = ("en", "fr", "zh-CN")
"""Language codes the MeshLite web SDK has a lip model for."""

AUTO = "auto"
"""MeshLite code that keeps the lip model of the avatar's own language."""

# Chinese variants spoken in Mandarin. Cantonese (``yue``, ``zh-HK``) is not Mandarin.
_MANDARIN = {"zh", "zh-cn", "zh-tw", "zh-sg", "cmn", "cmn-hans", "cmn-hant", "zh-hans", "zh-hant"}


def to_meshlite_language(language: Language | str | None) -> str | None:
    """Return the MeshLite lip-model code for a Pipecat language.

    Args:
        language: A Pipecat :class:`~pipecat.transcriptions.language.Language`, a BCP-47
            string such as ``"en-US"``, or ``None``.

    Returns:
        ``"en"``, ``"fr"`` or ``"zh-CN"`` when MeshLite has a lip model for the language,
        ``"auto"`` for any other language, and ``None`` when ``language`` is ``None``
        or empty.

    Examples:
        >>> to_meshlite_language(Language.EN_GB)
        'en'
        >>> to_meshlite_language("fr-CA")
        'fr'
        >>> to_meshlite_language("es")
        'auto'
    """
    if language is None:
        return None
    code = str(language.value if isinstance(language, Language) else language).strip()
    if not code:
        return None
    lowered = code.lower().replace("_", "-")
    if lowered == AUTO:
        return AUTO
    primary = lowered.split("-", 1)[0]
    if primary == "en":
        return "en"
    if primary == "fr":
        return "fr"
    if lowered in _MANDARIN or (primary in {"zh", "cmn"} and lowered not in {"zh-hk", "zh-mo"}):
        return "zh-CN"
    return AUTO


# Same rule as the MeshLite web SDK (sdk/realtime.js, speechLanguageOf, 1.11.58): the
# language the bot really speaks, read from the words it says. Chinese when ideograms
# dominate, otherwise French or English from common words and accents.
_FRENCH_WORDS = frozenset(
    "le la les des du de un une et est sont je tu il elle nous vous ils elles ce cette ces "
    "que qui ne pas pour dans avec sur au aux mais ou donc tres bien oui non merci bonjour "
    "c'est j'ai".split()
)
_ENGLISH_WORDS = frozenset(
    "the an and is are was were i you he she we they it this that these to of in for with "
    "not but or so very well yes thanks hello what how can will".split()
)
_HAN = re.compile("[㐀-鿿]")
_ACCENTS = re.compile("[éèêàâùûçôîïë]", re.IGNORECASE)
_WORDS = re.compile("[a-z']+")


def spoken_language_of(text: str) -> str | None:
    """Return the lip-model code of the language a text is written in, if it is clear.

    Args:
        text: What the bot said in its current reply, so far.

    Returns:
        ``"zh-CN"``, ``"fr"`` or ``"en"``, or ``None`` while the text is too short or
        mixed to tell (fewer than four telling words, or no two-to-one majority).

    Examples:
        >>> spoken_language_of("Bonjour, je suis content de vous aider.")
        'fr'
        >>> spoken_language_of("Hello, I am happy to help you with this.")
        'en'
    """
    han = len(_HAN.findall(text))
    letters = sum(1 for char in text if char.isalpha())
    if han >= 4 and han > letters * 0.3:
        return "zh-CN"
    french = len(_ACCENTS.findall(text)) * 0.5
    english = 0
    plain = "".join(
        char
        for char in unicodedata.normalize("NFD", text.lower())
        if not unicodedata.combining(char)
    )
    for word in _WORDS.findall(plain):
        if word in _FRENCH_WORDS:
            french += 1
        elif word in _ENGLISH_WORDS:
            english += 1
    if french + english < 4:
        return None
    if french >= 2 * english:
        return "fr"
    if english >= 2 * french:
        return "en"
    return None
