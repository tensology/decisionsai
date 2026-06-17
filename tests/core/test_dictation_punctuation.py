from distr.core.audio.dictation_text import trim_short_dictation_period


def test_trim_short_dictation_period_strips_five_words_or_fewer():
    assert trim_short_dictation_period("hello world.") == "hello world"
    assert trim_short_dictation_period("one two three four five.") == "one two three four five"


def test_trim_short_dictation_period_keeps_longer_sentences():
    text = "please update the auth middleware regression test."
    assert trim_short_dictation_period(text) == text


def test_trim_short_dictation_period_keeps_other_punctuation():
    assert trim_short_dictation_period("really?") == "really?"
    assert trim_short_dictation_period("wait...") == "wait..."


def test_trim_short_dictation_period_keeps_numeric_decimals():
    assert trim_short_dictation_period("v2.0") == "v2.0"
