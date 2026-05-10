from datetime import datetime
from typing import Literal

from pydantic import BaseModel


JobStatus = Literal["queued", "running", "uploading", "succeeded", "failed"]


class GenerationJobAccepted(BaseModel):
    job_id: str
    status: JobStatus
    feature: str
    message: str


class GenerationJobStatusResponse(BaseModel):
    job_id: str
    feature: str
    status: JobStatus
    stage: str | None = None
    model: str | None = None
    prompt: str | None = None
    message: str
    error_message: str | None = None
    result: dict[str, object] | None = None
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
