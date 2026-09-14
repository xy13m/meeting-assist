from meeting_assist.language import ChineseTarget, GenericTarget, for_code

SIMPLIFIED = "对，而且 license 只有一个 slot。"
TRADITIONAL = "對，而且 license 只有一個 slot。"


def test_zh_tw_converts_simplified_to_traditional():
    assert for_code("zh-TW").normalize(SIMPLIFIED) == TRADITIONAL


def test_zh_cn_leaves_text_unchanged():
    assert for_code("zh-CN").normalize(SIMPLIFIED) == SIMPLIFIED


def test_bare_zh_and_case_variants_mean_zh_tw():
    assert for_code("zh").code == "zh-TW"
    assert for_code("zh-tw").code == "zh-TW"
    assert for_code("ZH-CN").code == "zh-CN"


def test_chinese_target_names():
    assert for_code("zh-TW").name == "Traditional Chinese (Taiwan)"
    assert for_code("zh-CN").name == "Simplified Chinese"


def test_chinese_target_detects_mostly_chinese_turns():
    target = for_code("zh-TW")
    assert target.is_already_target(TRADITIONAL) is True
    assert target.is_already_target("Hello.") is False
    assert target.is_already_target("I said 你好 to him this morning.") is False
    assert target.is_already_target("") is False


def test_normalize_returns_plain_text_untouched():
    text = "Hello there."
    assert for_code("zh-TW").normalize(text) is text


def test_generic_target_for_known_code():
    target = for_code("en")
    assert isinstance(target, GenericTarget)
    assert target.code == "en"
    assert target.name == "English"
    assert target.normalize("你好") == "你好"
    assert target.is_already_target("Hello.") is False


def test_generic_target_for_unknown_code_uses_code_as_name():
    target = for_code("xx-YY")
    assert target.name == "xx-YY"


def test_chinese_target_is_a_target_language():
    assert isinstance(for_code("zh-TW"), ChineseTarget)
