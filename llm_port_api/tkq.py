from typing import Any

import taskiq_fastapi
from taskiq import AsyncBroker, InMemoryBroker
from taskiq_aio_pika import AioPikaBroker

from llm_port_api.settings import settings

broker: AsyncBroker

if settings.environment.lower() == "pytest":
    broker = InMemoryBroker()
else:
    _broker = AioPikaBroker(
        str(settings.rabbit_url),
        declare_exchange_kwargs={"durable": True},
    )
    if settings.redis_enabled:
        from taskiq_redis import RedisAsyncResultBackend

        _result_backend = RedisAsyncResultBackend(
            redis_url=str(settings.redis_url.with_path("/1")),
        )
        _broker = _broker.with_result_backend(_result_backend)
    broker = _broker

taskiq_fastapi.init(
    broker,
    "llm_port_api.web.application:get_app",
)
