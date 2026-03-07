from taskiq import AsyncBroker, InMemoryBroker

# PII is a stateless Presidio wrapper — no background tasks or queue workers.
# IMPORTANT: If this is ever changed to AioPikaBroker, use a unique
# queue_name (e.g. "taskiq.pii") to avoid stealing messages from other services.
broker: AsyncBroker = InMemoryBroker()
