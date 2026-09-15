"""Inference driver implementations (Phase 1: none registered).

A *driver* adapts a concrete backend (today: Ray) to the neutral contracts in
:mod:`llm_port_backend.services.inference.contracts`.  Phase 2 will add
``ray.py`` here with, for example::

    from llm_port_backend.services.inference.contracts import InferenceDriver
    from llm_port_backend.services.inference.registry import registry

    class RayDriver(InferenceDriver):
        key = "ray"
        # ... async probe(control_plane) / capabilities(environment) ...

    registry.register(RayDriver.key, RayDriver)

No driver is registered in Phase 1, so the neutral domain stays fully backend-
agnostic and carries no Ray dependency.
"""
