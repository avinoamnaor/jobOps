"""Health endpoints.

Two endpoints, because "is it up?" and "can it work?" are different questions and
conflating them makes both useless:

  GET /health     Liveness. Is the process running and able to answer? Touches
                  nothing external, so it can never fail because of a dependency.

  GET /health/db  Readiness. Can we actually reach PostgreSQL right now? Returns
                  503 when we cannot, which is the honest answer.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db

router = APIRouter(tags=["health"])


@router.get("/health")
def liveness() -> dict[str, str]:
    return {"status": "ok", "app": "jobops", "env": settings.app_env}


@router.get("/health/db")
def readiness(db: Session = Depends(get_db)) -> dict[str, str]:
    try:
        # `text()` is required because SQLAlchemy 2.0 refuses to execute raw
        # strings — an explicit guard against accidental SQL injection.
        db.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="database unavailable",
        ) from exc
    return {"status": "ok", "database": "ok"}
