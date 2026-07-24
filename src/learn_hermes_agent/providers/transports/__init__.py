from __future__ import annotations

from learn_hermes_agent.providers.transports.base import (
    ProviderTransport,
)

_TRANSPORT_REGISTRY: dict[
    str,
    type[ProviderTransport],
] = {}


def _normalize_api_mode(api_mode: str) -> str:
    normalized = str(api_mode).strip().lower()
    if not normalized:
        raise ValueError("api_mode must not be empty")
    return normalized


def register_transport(
        api_mode: str,
        transport_cls: type[ProviderTransport],
) -> None:
    normalized = _normalize_api_mode(api_mode)
    existing = _TRANSPORT_REGISTRY.get(normalized)

    if existing is not None and existing is not transport_cls:
        raise ValueError(
            "Transport already registered for "
            f"api_mode {normalized!r}"
        )

    _TRANSPORT_REGISTRY[normalized] = transport_cls


def get_transport(api_mode: str) -> ProviderTransport:
    normalized = _normalize_api_mode(api_mode)
    transport_cls = _TRANSPORT_REGISTRY.get(normalized)

    if transport_cls is None:
        raise ValueError(
            f"Unsupported provider api_mode: {normalized!r}"
        )

    return transport_cls()


__all__ = [
    "ProviderTransport",
    "get_transport",
    "register_transport",
]
