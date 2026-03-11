import taskiq_fastapi
from taskiq import AsyncBroker, InMemoryBroker
from taskiq_aio_pika import AioPikaBroker

from llm_port_pii.settings import settings

broker: AsyncBroker = AioPikaBroker(
    str(settings.rabbit_url),
    queue_name="taskiq.pii",
    qos=1,
    declare_exchange_kwargs={"durable": True},
)

if settings.environment.lower() == "pytest":
    broker = InMemoryBroker()

taskiq_fastapi.init(
    broker,
    "llm_port_pii.web.application:get_app",
)
