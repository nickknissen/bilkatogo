# Home Assistant aliases httpx to httpx2 at runtime, and its httpx_client helper returns
# httpx2 clients. Like core's stubs/httpx, re-export httpx2 under the httpx name so mypy
# accepts them where the library expects httpx. Only the ha group's mypy run reads this.
from httpx2 import *  # noqa: F403
