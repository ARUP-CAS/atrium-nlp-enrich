import asyncio
import time
from dataclasses import dataclass, field
from typing import Dict, Optional
import uuid

@dataclass
class Job:
    job_id: str
    status: str  # "queued" | "running" | "done" | "failed"
    result: Optional[dict] = None
    error: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None
    workspace: Optional[str] = None

_jobs: Dict[str, Job] = {}
_job_lock = asyncio.Lock()

async def create_job() -> Job:
    job_id = uuid.uuid4().hex
    job = Job(job_id=job_id, status="queued")
    async with _job_lock:
        _jobs[job_id] = job
    return job