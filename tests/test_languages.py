import pytest
from pipecat.transcriptions.language import Language

from pipecat_meshlite import to_meshlite_language


@pytest.mark.parametrize(
    "value, expected",
    [
        (None, None),
        ("", None),
        (Language.EN, "en"),
        (Language.EN_GB, "en"),
        ("en_US", "en"),
        (Language.FR_CA, "fr"),
        (Language.ZH, "zh-CN"),
        ("zh-TW", "zh-CN"),
        ("cmn-Hans-CN", "zh-CN"),
        ("zh-HK", "auto"),
        ("yue", "auto"),
        (Language.ES, "auto"),
        ("de-DE", "auto"),
        ("auto", "auto"),
    ],
)
def test_maps_languages(value, expected):
    assert to_meshlite_language(value) == expected
