"""Bot texts: every shipped language has every text, with the same placeholders, valid Telegram HTML,
and right-to-left languages get the direction mark."""
import re
import pytest
from backend.services import rich
from backend.services.i18n import Translator, builtin, builtin_keys
from backend.services.setup import LANGUAGES

PLACEHOLDER = re.compile(r"{(\w+)}")


def test_all_languages_are_shipped():
    assert set(LANGUAGES) <= set(builtin())


@pytest.mark.parametrize("code", LANGUAGES)
def test_language_is_complete(code):
    english, texts = builtin()["en"], builtin()[code]
    missing = [k for k in builtin_keys() if not texts.get(k)]
    assert not missing, f"{code}: {missing}"
    for key in builtin_keys():
        assert set(PLACEHOLDER.findall(texts[key])) == set(PLACEHOLDER.findall(english[key])), f"{code}.{key}"
        assert rich.check_html(texts[key]), f"{code}.{key}"


def test_rtl_mark_and_fallback():
    t = Translator(rtl={"ar", "he", "ur"})
    assert t("ar", "menu_title").startswith("‏")
    assert not t("ru", "menu_title").startswith("‏")
    assert t("xx", "menu_title") == builtin()["en"]["menu_title"]  # unknown language: English, never the key
    assert t("en", "no_such_key") == ""
