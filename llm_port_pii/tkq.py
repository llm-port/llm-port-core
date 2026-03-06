from taskiq import AsyncBroker, InMemoryBroker

# PII is a stateless Presidio wrapper — no background tasks or queue workers.
broker: AsyncBroker = InMemoryBroker()
