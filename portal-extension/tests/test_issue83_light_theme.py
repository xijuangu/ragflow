"""Issue 83: Portal embeds declare a light default without forcing a theme."""

from urllib.parse import parse_qs, urlparse

from portal.gateway import build_agent_iframe_url, build_iframe_url


def test_chat_iframe_url_declares_light_as_its_default_theme():
    iframe_url = build_iframe_url("", "dialog-1", "pt_test")

    params = parse_qs(urlparse(iframe_url).query)
    assert params["default_theme"] == ["light"]
    assert "theme" not in params, "Portal must not override an explicit RAGFlow theme choice"


def test_agent_iframe_url_declares_light_as_its_default_theme():
    iframe_url = build_agent_iframe_url("", "agent-1", "pt_test")

    params = parse_qs(urlparse(iframe_url).query)
    assert params["default_theme"] == ["light"]
    assert "theme" not in params, "Portal must not override an explicit RAGFlow theme choice"
