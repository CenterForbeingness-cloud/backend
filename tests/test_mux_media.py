"""Unit tests for Mux media playback helpers."""

import pytest

from app import mux_media


def test_build_hls_url_public():
    url = mux_media.build_hls_url("abc123")
    assert url == "https://stream.mux.com/abc123.m3u8"


def test_build_hls_url_signed():
    url = mux_media.build_hls_url("abc123", token="jwt.token.here")
    assert url == "https://stream.mux.com/abc123.m3u8?token=jwt.token.here"


def test_resolve_playback_meditate_in_5(monkeypatch):
    monkeypatch.setattr(mux_media, "MUX_SIGNED_PLAYBACK", False)
    monkeypatch.setattr(
        mux_media,
        "MUX_PLAYBACK_BY_CONTENT_KEY",
        {"meditate-in-5": "00HJmW8D0246aAezlSXDaKKfUZTcBAD02KBoJhaEsfzAsU"},
    )

    result = mux_media.resolve_playback("meditate-in-5")
    assert result["content_key"] == "meditate-in-5"
    assert result["title"] == "Meditate in 5 Minutes"
    assert result["playback_id"] == "00HJmW8D0246aAezlSXDaKKfUZTcBAD02KBoJhaEsfzAsU"
    assert (
        result["playback_url"]
        == "https://stream.mux.com/00HJmW8D0246aAezlSXDaKKfUZTcBAD02KBoJhaEsfzAsU.m3u8"
    )
    assert result["signed"] is False
    assert result["host"] == "mux"


def test_resolve_playback_unknown(monkeypatch):
    monkeypatch.setattr(mux_media, "MUX_PLAYBACK_BY_CONTENT_KEY", {})
    with pytest.raises(KeyError):
        mux_media.resolve_playback("does-not-exist")
