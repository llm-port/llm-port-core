import taskiq_fastapi
from taskiq import AsyncBroker, InMemoryBroker
from taskiq_aio_pika import AioPikaBroker

from llm_port_backend.settings import settings

broker: AsyncBroker = AioPikaBroker(
    str(settings.rabbit_url),
    queue_name="taskiq.backend",
    # How many messages a worker process takes at once, and so how many tasks
    # it runs at once. It was 1: documents were ingested one after another,
    # ~8 a second, each waiting on its own embedding call -- and a model
    # download held a whole worker for its duration. Messages are acknowledged
    # after the task has run (taskiq's WHEN_SAVED), so a crash returns every
    # message in flight to the queue, whatever this is; ingestion replaces a
    # document's chunks, so running one again is harmless.
    qos=settings.taskiq_prefetch,
    declare_exchange_kwargs={"durable": True},
)

if settings.environment.lower() == "pytest":
    broker = InMemoryBroker()

taskiq_fastapi.init(
    broker,
    "llm_port_backend.web.application:get_app",
)
