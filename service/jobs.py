import asyncio
from dataclasses import dataclass
from typing import Dict, Optional
import uuid

@dataclass
class Job:
    job_id: str
    status: str  # "queued" | "running" | "done" | "failed"
    result: Optional[dict] = None
    error: Optional[str] = None

_jobs: Dict[str, Job] = {}
_job_lock = asyncio.Lock()

async def create_job() -> Job:
    job_id = uuid.uuid4().hex
    job = Job(job_id=job_id, status="queued")
    async with _job_lock:
        _jobs[job_id] = job
    return job