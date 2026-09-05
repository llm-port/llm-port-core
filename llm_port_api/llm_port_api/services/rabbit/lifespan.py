import ssl

import aio_pika
from aio_pika.abc import AbstractChannel, AbstractRobustConnection
from aio_pika.pool import Pool
from fastapi import FastAPI

from llm_port_api.services.tls import rewrite_amqp_url_for_tls
from llm_port_api.settings import settings


def _build_rabbit_kwargs() -> dict:
    kwargs: dict = {}
    if settings.rabbit_ssl:
        kwargs["ssl_context"] = ssl.create_default_context(
            cafile=settings.rabbit_ssl_ca_bundle,
        )
    return kwargs


def init_rabbit(app: FastAPI) -> None:  # pragma: no cover
    """
    Initialize rabbitmq pools.

    :param app: current FastAPI application.
    """

    async def get_connection() -> AbstractRobustConnection:
        """
        Creates connection to RabbitMQ using url from settings.

        :return: async connection to RabbitMQ.
        """
        url = rewrite_amqp_url_for_tls(str(settings.rabbit_url), settings.rabbit_ssl)
        return await aio_pika.connect_robust(url, **_build_rabbit_kwargs())

    # This pool is used to open connections.
    connection_pool: Pool[AbstractRobustConnection] = Pool(
        get_connection,
        max_size=settings.rabbit_pool_size,
    )

    async def get_channel() -> AbstractChannel:
        """
        Open channel on connection.

        Channels are used to actually communicate with rabbitmq.

        :return: connected channel.
        """
        async with connection_pool.acquire() as connection:
            return await connection.channel()

    # This pool is used to open channels.
    channel_pool: Pool[aio_pika.Channel] = Pool(
        get_channel,
        max_size=settings.rabbit_channel_pool_size,
    )

    app.state.rmq_pool = connection_pool
    app.state.rmq_channel_pool = channel_pool


async def shutdown_rabbit(app: FastAPI) -> None:  # pragma: no cover
    """
    Close all connection and pools.

    :param app: current application.
    """
    await app.state.rmq_channel_pool.close()
    await app.state.rmq_pool.close()
