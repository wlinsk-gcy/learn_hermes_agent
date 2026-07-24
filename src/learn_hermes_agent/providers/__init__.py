from __future__ import annotations

from learn_hermes_agent.providers.base import ProviderProfile

_PROVIDER_PROFILES: dict[str, ProviderProfile] = {}


def _normalize_provider_name(name: str) -> str:
    normalized = str(name).strip().lower()
    if not normalized:
        raise ValueError("Provider name must not be empty")
    return normalized


def register_provider_profile(
        profile: ProviderProfile,
) -> None:
    keys = (profile.name, *profile.aliases)
    normalized_keys = tuple(
        _normalize_provider_name(key)
        for key in keys
    )

    for key in normalized_keys:
        existing = _PROVIDER_PROFILES.get(key)
        if existing is not None and existing is not profile:
            raise ValueError(
                "Provider profile name or alias "
                f"already registered: {key!r}"
            )

    for key in normalized_keys:
        _PROVIDER_PROFILES[key] = profile


def get_provider_profile(name: str) -> ProviderProfile:
    normalized = _normalize_provider_name(name)
    profile = _PROVIDER_PROFILES.get(normalized)
    if profile is None:
        raise ValueError(
            f"Unsupported model provider: {normalized!r}"
        )
    return profile


register_provider_profile(
    ProviderProfile(
        name="fake",
        api_mode="chat_completions",
        requires_api_key=False,
    )
)

register_provider_profile(
    ProviderProfile(
        name="openai-compatible",
        api_mode="chat_completions",
        aliases=("openai",),
        default_base_url="https://api.openai.com/v1",
        api_key_env="OPENAI_API_KEY",
    )
)
register_provider_profile(
    ProviderProfile(
        name="openrouter",
        api_mode="chat_completions",
        default_base_url=(
            "https://openrouter.ai/api/v1"
        ),
        api_key_env="OPENROUTER_API_KEY",
    )
)

__all__ = [
    "ProviderProfile",
    "get_provider_profile",
    "register_provider_profile",
]