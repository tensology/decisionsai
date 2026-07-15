from distr.gui.web.routes.settings.advanced import normalize_telegram_deep_link


def test_t_me_bot_link_uses_reachable_telegram_domain():
    link = "https://t.me/decisionsai_bot?start=abc123"
    assert normalize_telegram_deep_link(link) == (
        "https://telegram.me/decisionsai_bot?start=abc123"
    )


def test_www_t_me_link_preserves_path_query_and_fragment():
    link = "http://www.t.me/example_bot?start=a-b_c#open"
    assert normalize_telegram_deep_link(link) == (
        "https://telegram.me/example_bot?start=a-b_c#open"
    )


def test_non_telegram_and_native_links_are_unchanged():
    assert normalize_telegram_deep_link("https://example.com/t.me/bot") == "https://example.com/t.me/bot"
    assert normalize_telegram_deep_link("tg://resolve?domain=example_bot") == "tg://resolve?domain=example_bot"
    assert normalize_telegram_deep_link(None) is None
