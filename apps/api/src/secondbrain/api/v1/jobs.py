import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from secondbrain.db.engine import get_session
from secondbrain.queue import queue
from secondbrain.schemas.documents import JobOut

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("/{job_id}", response_model=JobOut, summary="Job status")
def get_job(session: Annotated[Session, Depends(get_session)], job_id: uuid.UUID) -> JobOut:
    job = queue.get(session, job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    return JobOut.model_validate(job)
