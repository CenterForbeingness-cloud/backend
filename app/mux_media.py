"""
mux_media.py — Mux Video playback for Foundations media.

Playback IDs map to product content keys. The Flutter app never talks to Mux
with API secrets; it asks this backend for an HLS URL.
"""

from __future__ import annotations

from typing import Optional

from app.config import (
    MUX_PLAYBACK_BY_CONTENT_KEY,
    MUX_SIGNED_PLAYBACK,
    MUX_SIGNING_KEY_ID,
    MUX_SIGNING_PRIVATE_KEY,
    MUX_TOKEN_ID,
    MUX_TOKEN_SECRET,
    logger,
)

# Durable free-after-trial media keys (locked product decisions).
FOUNDATIONS_MEDIA_KEYS: frozenset[str] = frozenset({"meditate-in-5"})

_CONTENT_TITLES: dict[str, str] = {
    "meditate-in-5": "Meditate in 5 Minutes",
}


def mux_credentials_configured() -> bool:
    return bool(MUX_TOKEN_ID and MUX_TOKEN_SECRET)


def list_media_catalog() -> list[dict]:
    """Return content keys that have a playback ID configured."""
    items: list[dict] = []
    for content_key, playback_id in MUX_PLAYBACK_BY_CONTENT_KEY.items():
        items.append(
            {
                "content_key": content_key,
                "title": _CONTENT_TITLES.get(content_key, content_key),
                "playback_id": playback_id,
                "host": "mux",
            }
        )
    return items


def get_playback_id(content_key: str) -> Optional[str]:
    key = (content_key or "").strip().lower()
    return MUX_PLAYBACK_BY_CONTENT_KEY.get(key)


def build_hls_url(playback_id: str, token: Optional[str] = None) -> str:
    base = f"https://stream.mux.com/{playback_id}.m3u8"
    if token:
        return f"{base}?token={token}"
    return base


def _sign_playback_jwt(playback_id: str, expires_in_seconds: int = 14400) -> str:
    """Sign a Mux playback JWT. Requires MUX_SIGNING_KEY_ID and private key."""
    import time

    import jwt

    if not MUX_SIGNING_KEY_ID or not MUX_SIGNING_PRIVATE_KEY:
        raise RuntimeError(
            "MUX_SIGNED_PLAYBACK is on but MUX_SIGNING_KEY_ID / "
            "MUX_SIGNING_PRIVATE_KEY are not configured"
        )

    private_key = MUX_SIGNING_PRIVATE_KEY
    if "BEGIN" not in private_key and "\\n" in private_key:
        private_key = private_key.replace("\\n", "\n")

    now = int(time.time())
    payload = {
        "sub": playback_id,
        "aud": "v",
        "exp": now + expires_in_seconds,
        "kid": MUX_SIGNING_KEY_ID,
    }
    return jwt.encode(
        payload,
        private_key,
        algorithm="RS256",
        headers={"kid": MUX_SIGNING_KEY_ID},
    )


def resolve_playback(content_key: str) -> dict:
    """
    Resolve an HLS playback URL for a content key.

    Public playback is the default until signing keys are configured and
    MUX_SIGNED_PLAYBACK=true.
    """
    key = (content_key or "").strip().lower()
    playback_id = get_playback_id(key)
    if not playback_id:
        raise KeyError(f"Unknown media content key: {content_key}")

    signed = False
    token: Optional[str] = None
    if MUX_SIGNED_PLAYBACK:
        token = _sign_playback_jwt(playback_id)
        signed = True

    return {
        "content_key": key,
        "title": _CONTENT_TITLES.get(key, key),
        "playback_id": playback_id,
        "playback_url": build_hls_url(playback_id, token),
        "signed": signed,
        "host": "mux",
    }


def mux_assets_api_ready() -> dict:
    """Light readiness probe using the official Mux Python SDK when configured."""
    if not mux_credentials_configured():
        return {"configured": False, "ok": False, "detail": "MUX_TOKEN_ID/SECRET missing"}

    try:
        import mux_python
        from mux_python.rest import ApiException

        configuration = mux_python.Configuration()
        configuration.username = MUX_TOKEN_ID
        configuration.password = MUX_TOKEN_SECRET
        assets_api = mux_python.AssetsApi(mux_python.ApiClient(configuration))
        assets_api.list_assets(limit=1)
        return {"configured": True, "ok": True, "detail": "Mux Assets API reachable"}
    except Exception as exc:  # noqa: BLE001 — surface as readiness detail
        logger.warning("Mux Assets API probe failed: %s", exc)
        return {
            "configured": True,
            "ok": False,
            "detail": str(exc)[:240],
        }
