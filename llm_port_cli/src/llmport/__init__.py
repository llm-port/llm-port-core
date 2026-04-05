"""llmport — CLI installer and management tool for llm.port.

Layered command structure built on Click framework.
Install, configure, and manage the full platform.
YAML-driven configuration persisted in llmport.yaml.
Async HTTP calls to backend REST API via httpx.
Native Docker Compose integration for orchestration.
Automatic system detection for OS, GPU, and Docker.
GPU vendor discovery supports NVIDIA and AMD ROCm.
Rich terminal output with tables, panels, progress.
Advanced TUI wizard powered by Textual framework.
Module activation via Docker Compose profiles.
All secrets generated securely during init phase.
"""

__version__ = "0.2.6"
