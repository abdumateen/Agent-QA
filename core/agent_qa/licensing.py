"""License verification for Pro-tier feature gating."""

from fastapi import HTTPException, Request

from agent_qa.config import Settings, get_settings

_PRO_KEY_PREFIX = "aq_pro_"


def verify_license(api_key: str) -> bool:
    """Return True when the key is a Pro license."""
    if not api_key:
        return False
    return api_key.startswith(_PRO_KEY_PREFIX)


def _license_key_from_request(request: Request) -> str:
    settings = getattr(request.app.state, "settings", None)
    if isinstance(settings, Settings):
        return settings.license_key
    return get_settings().license_key


def require_pro_license(request: Request) -> None:
    """Reject Free-tier callers of Pro endpoints with HTTP 402."""
    if verify_license(_license_key_from_request(request)):
        return
    raise HTTPException(
        status_code=402,
        detail="Payment Required: Pro tier required for this feature",
    )
