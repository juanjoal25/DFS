"""Helpers HTTP compartidos."""
import httpx

DEFAULT_TIMEOUT = httpx.Timeout(connect=10.0, read=300.0, write=300.0, pool=10.0)


def client(**kwargs) -> httpx.Client:
    kwargs.setdefault("timeout", DEFAULT_TIMEOUT)
    return httpx.Client(**kwargs)


async def aclient(**kwargs) -> httpx.AsyncClient:
    kwargs.setdefault("timeout", DEFAULT_TIMEOUT)
    return httpx.AsyncClient(**kwargs)
