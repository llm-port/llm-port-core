"""A named RabbitMQ vhost works (found starting a second stack on vhost "e2e")."""

from llm_port_backend.settings import Settings


def test_a_named_vhost_is_the_urls_path() -> None:
    url = Settings(_env_file=None, rabbit_vhost="llmport").rabbit_url
    assert url.path == "/llmport"


def test_the_default_vhost_stays_the_root() -> None:
    assert Settings(_env_file=None, rabbit_vhost="/").rabbit_url.path == "/"
