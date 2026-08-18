"""FastAPI application entry point.

Deliberately thin. Its only jobs are to create the app, apply middleware, map
domain errors onto HTTP status codes, and wire up routers. Business logic lives
in `app/services/`; routers only translate between HTTP and those services.
"""

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import applications, documents, health, meta
from app.config import settings
from app.core.errors import (
    ApplicationNotFound,
    DocumentFileMissing,
    DocumentKindNotAllowed,
    DocumentNotFound,
    DocumentTooLarge,
    EmptyDocument,
    StatusUnchanged,
    SubmittedCvUnchanged,
    UnsafeDocumentPath,
)

app = FastAPI(
    title="JobOps API",
    version="0.1.0",
    description="Personal job-application tracker.",
)

# The browser blocks a page served from localhost:5173 (the React dev server)
# from calling an API on localhost:8000 unless the API explicitly opts in.
# That opt-in is CORS. The allowed origins come from configuration, so this
# stays correct if the frontend ever moves.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- Domain errors -> HTTP status codes -----------------------------------
# Registered here so that services can raise meaningful domain exceptions
# without importing FastAPI. The translation happens once, at the edge.


@app.exception_handler(ApplicationNotFound)
def handle_application_not_found(_: Request, exc: ApplicationNotFound) -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": str(exc)})


@app.exception_handler(StatusUnchanged)
def handle_status_unchanged(_: Request, exc: StatusUnchanged) -> JSONResponse:
    # 409 Conflict: the request was well-formed, but it conflicts with the
    # current state of the resource.
    return JSONResponse(status_code=409, content={"detail": str(exc)})


@app.exception_handler(DocumentNotFound)
def handle_document_not_found(_: Request, exc: DocumentNotFound) -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": str(exc)})


@app.exception_handler(SubmittedCvUnchanged)
def handle_submitted_cv_unchanged(_: Request, exc: SubmittedCvUnchanged) -> JSONResponse:
    return JSONResponse(status_code=409, content={"detail": str(exc)})


@app.exception_handler(DocumentKindNotAllowed)
def handle_document_kind_not_allowed(_: Request, exc: DocumentKindNotAllowed) -> JSONResponse:
    # 422: the request was understood, but the entity it referenced is not
    # usable in this position.
    return JSONResponse(status_code=422, content={"detail": str(exc)})


@app.exception_handler(EmptyDocument)
def handle_empty_document(_: Request, exc: EmptyDocument) -> JSONResponse:
    return JSONResponse(status_code=422, content={"detail": str(exc)})


@app.exception_handler(DocumentTooLarge)
def handle_document_too_large(_: Request, exc: DocumentTooLarge) -> JSONResponse:
    # 413 Content Too Large.
    return JSONResponse(status_code=413, content={"detail": str(exc)})


@app.exception_handler(DocumentFileMissing)
def handle_document_file_missing(_: Request, exc: DocumentFileMissing) -> JSONResponse:
    # 500, honestly: the metadata promised a file that storage does not have.
    # That is a server-side integrity problem, not a client mistake — but it is
    # reported as a clear message rather than an unhandled traceback.
    return JSONResponse(status_code=500, content={"detail": str(exc)})


@app.exception_handler(UnsafeDocumentPath)
def handle_unsafe_document_path(_: Request, exc: UnsafeDocumentPath) -> JSONResponse:
    return JSONResponse(status_code=500, content={"detail": str(exc)})


app.include_router(health.router)
app.include_router(meta.router)
app.include_router(applications.router)
app.include_router(documents.router)
