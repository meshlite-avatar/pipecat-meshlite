"""Map Pipecat languages to the lip models of the MeshLite web SDK.

The face's lips are computed in the visitor's browser by a small speech model trained
for one language. The SDK has one model each for English, French and Mandarin Chinese;
any other language keeps the model of the avatar's own language (``"auto"``).
"""

from __future__ import annotations

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
