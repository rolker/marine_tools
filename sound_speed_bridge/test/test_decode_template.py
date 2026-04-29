"""Tests for sound_speed_bridge.sinks.decode_template (startup validation)."""

import pytest

from sound_speed_bridge.sinks import decode_template


def test_decode_template_passes_non_template_formats_through():
    """Non-template formats keep their template string unchanged (unused)."""
    assert decode_template('valeport', '') == ''
    assert decode_template('passthrough', 'whatever') == 'whatever'


def test_decode_template_decodes_backslash_escapes_for_template_format():
    r"""\r\n in a YAML/CLI template parameter becomes real CRLF at startup."""
    assert decode_template('template', '{value:.1f}\\r\\n') == '{value:.1f}\r\n'


def test_decode_template_empty_returns_empty():
    """An empty template stays empty (the formatter treats it as misconfigured)."""
    assert decode_template('template', '') == ''


def test_decode_template_rejects_non_ascii_at_startup():
    """A non-ASCII char in the template raises ValueError at config time."""
    with pytest.raises(ValueError):
        decode_template('template', 'speedµ={value}')


def test_decode_template_rejects_malformed_escape_at_startup():
    r"""A truncated \x escape raises ValueError at config time, not mid-stream."""
    with pytest.raises(ValueError):
        decode_template('template', '{value}\\xZZ')
