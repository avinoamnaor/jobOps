"""Shared base class for request schemas."""

from pydantic import BaseModel, ConfigDict


class StrictModel(BaseModel):
    """Base for every schema that parses an incoming request body.

    `extra="forbid"` tells Pydantic to reject any field it does not declare;
    FastAPI turns that rejection into a 422.

    Pydantic's default is to *ignore* unknown fields, and that default is
    actively dangerous here. `ApplicationUpdate` deliberately has no `status`
    field, so under the default a PATCH sending `status` would return 200 OK
    while the status quietly did not change — the caller would believe it
    worked. Failing loudly is the difference between "the API told me no" and
    "I thought that worked".

    It also catches ordinary mistakes: a typo like `compnay_name` becomes an
    immediate 422 instead of a silently unsaved field.

    Response schemas deliberately do NOT inherit from this — they are built from
    ORM objects, not parsed from user input, so there is nothing to forbid.
    """

    model_config = ConfigDict(extra="forbid")
