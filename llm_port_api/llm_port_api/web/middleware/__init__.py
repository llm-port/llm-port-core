"""HTTP middleware for the API."""

from llm_port_api.web.middleware.security import register_security_middleware

__all__ = ["register_security_middleware"]
