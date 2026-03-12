from fastapi import APIRouter

from llm_port_backend.settings import settings

router = APIRouter()


@router.get("/health")
def health_check() -> dict[str, str]:
    """
    Checks the health of a project.

    It returns 200 if the project is healthy.
    """
    return {"status": "ok", "environment": settings.environment}
