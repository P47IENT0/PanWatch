from __future__ import annotations

import httpx

from src.config import Settings


def _normalize_proxy(proxy: str | None) -> str | None:
    if not proxy:
        return None
    value = proxy.strip()
    if not value:
        return None
    if value.startswith("socks://"):
        return "socks5://" + value[len("socks://") :]
    return value


def resolve_proxy(proxy: str | None = None) -> str | None:
    if proxy is not None:
        return _normalize_proxy(proxy)
    return _normalize_proxy(Settings().http_proxy)


def sync_client(*, proxy: str | None = None, **kwargs) -> httpx.Client:
    return httpx.Client(trust_env=False, proxy=resolve_proxy(proxy), **kwargs)


def async_client(*, proxy: str | None = None, **kwargs) -> httpx.AsyncClient:
    return httpx.AsyncClient(trust_env=False, proxy=resolve_proxy(proxy), **kwargs)


def sync_get(url: str, **kwargs) -> httpx.Response:
    if "trust_env" not in kwargs:
        kwargs["trust_env"] = False
    if "proxy" not in kwargs:
        kwargs["proxy"] = resolve_proxy(None)
    else:
        kwargs["proxy"] = resolve_proxy(kwargs.get("proxy"))
    return httpx.get(url, **kwargs)
