"""HTTP layer for applications.

Every function here does the same three things: read the request, call one
service function, return the result. No business rules — if you find yourself
writing an `if` about domain meaning in this file, it belongs in the service.
"""

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.enums import ApplicationChannel, ApplicationStatus
from app.schemas.application import (
    ApplicationCreate,
    ApplicationDetail,
    ApplicationPage,
    ApplicationRead,
    ApplicationUpdate,
    StatusChangeRequest,
)
from app.schemas.document import AttachSubmittedCvRequest
from app.schemas.event import EventCreate, EventRead
from app.services import applications as application_service
from app.services import events as event_service

router = APIRouter(prefix="/applications", tags=["applications"])


@router.post("", response_model=ApplicationRead, status_code=status.HTTP_201_CREATED)
def create_application(payload: ApplicationCreate, db: Session = Depends(get_db)) -> object:
    return application_service.create_application(db, payload)


@router.get("", response_model=ApplicationPage)
def list_applications(
    db: Session = Depends(get_db),
    status_filter: ApplicationStatus | None = Query(default=None, alias="status"),
    channel: ApplicationChannel | None = Query(default=None),
    q: str | None = Query(default=None, description="Matches company name or role title"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
) -> ApplicationPage:
    items, total = application_service.list_applications(
        db, status=status_filter, channel=channel, query=q, page=page, page_size=page_size
    )
    return ApplicationPage(
        items=[ApplicationRead.model_validate(item) for item in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/{application_id}", response_model=ApplicationDetail)
def get_application(application_id: int, db: Session = Depends(get_db)) -> object:
    return application_service.get_application(db, application_id, with_events=True)


@router.patch("/{application_id}", response_model=ApplicationRead)
def update_application(
    application_id: int,
    payload: ApplicationUpdate,
    db: Session = Depends(get_db),
) -> object:
    """Edit descriptive fields.

    `status` is not part of `ApplicationUpdate`, so sending it here does nothing.
    Status changes must go through POST /applications/{id}/status, which
    guarantees a timeline event is written.
    """
    return application_service.update_application(db, application_id, payload)


@router.delete("/{application_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_application(application_id: int, db: Session = Depends(get_db)) -> Response:
    application_service.soft_delete_application(db, application_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{application_id}/status", response_model=ApplicationDetail)
def change_status(
    application_id: int,
    payload: StatusChangeRequest,
    db: Session = Depends(get_db),
) -> object:
    """Change status and record it on the timeline, atomically.

    A dedicated endpoint rather than a PATCH field: it makes the rule visible in
    the API surface, and it means no generic update path can bypass the history.
    """
    application_service.change_status(
        db,
        application_id,
        to_status=payload.to,
        note=payload.note,
        occurred_at=payload.occurred_at,
    )
    return application_service.get_application(db, application_id, with_events=True)


@router.put("/{application_id}/submitted-cv", response_model=ApplicationDetail)
def attach_submitted_cv(
    application_id: int,
    payload: AttachSubmittedCvRequest,
    db: Session = Depends(get_db),
) -> object:
    """Set (or change) the CV recorded as submitted for this application.

    The service enforces that the document is of kind `cv` — this router does no
    checking of its own beyond parsing the body.
    """
    application_service.attach_submitted_cv(
        db,
        application_id,
        payload.document_id,
        note=payload.note,
        occurred_at=payload.occurred_at,
    )
    return application_service.get_application(db, application_id, with_events=True)


@router.get("/{application_id}/events", response_model=list[EventRead])
def list_events(application_id: int, db: Session = Depends(get_db)) -> object:
    return event_service.list_events(db, application_id)


@router.post(
    "/{application_id}/events",
    response_model=EventRead,
    status_code=status.HTTP_201_CREATED,
)
def add_event(
    application_id: int,
    payload: EventCreate,
    db: Session = Depends(get_db),
) -> object:
    return event_service.add_manual_event(db, application_id, payload)
