import os
from fastapi import FastAPI, BackgroundTasks, HTTPException, Header
from pydantic import BaseModel
from automation_runner import run_job

app = FastAPI()


class JobRequest(BaseModel):
    jobId: str


WORKER_AUTH_TOKEN = os.getenv("WORKER_AUTH_TOKEN")  # shared secret from Supabase


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/jobs/start")
async def start_job(
    payload: JobRequest,
    background: BackgroundTasks,
    authorization: str | None = Header(default=None),  # reads Authorization header
):
    # Simple shared-secret auth
    if WORKER_AUTH_TOKEN:
        expected = f"Bearer {WORKER_AUTH_TOKEN}"
        if not authorization or authorization != expected:
            raise HTTPException(status_code=401, detail="Unauthorized")

    background.add_task(run_job, payload.jobId)
    return {"status": "queued", "jobId": payload.jobId}
