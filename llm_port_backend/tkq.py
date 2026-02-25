import taskiq_fastapi
from taskiq import AsyncBroker, InMemoryBroker
from taskiq_aio_pika import AioPikaBroker

from llm_port_backend.settings import settings

broker: AsyncBroker = AioPikaBroker(
    str(settings.rabbit_url),
    # Each worker prefetches only 1 message at a time so that if the worker
    # crashes, only 1 message is returned to the queue for redelivery.
    qos=1,
)

if settings.environment.lower() == "pytest":
    broker = InMemoryBroker()

taskiq_fastapi.init(
    broker,
    "llm_port_backend.web.application:get_app",
)
