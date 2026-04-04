"""Cache adapter layer — abstracts Redis behind a protocol.

Core deployments without Redis get ``NoOpCache`` (fail-open).
Enterprise / performance tiers get ``RedisCache``.
"""

from llm_port_api.services.cache.noop import NoOpCache
from llm_port_api.services.cache.protocol import CacheBackend
from llm_port_api.services.cache.redis import RedisCache

__all__ = ["CacheBackend", "NoOpCache", "RedisCache"]
