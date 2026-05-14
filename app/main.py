import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("classiq.api")


class TaskCreate(BaseModel):
    qc: str


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("api.alive")
    yield
    logger.info("api.shutdown")


app = FastAPI(title="Classiq QASM Runner", version="0.1.0", lifespan=lifespan)


@app.get("/healthz")
async def healthz() -> dict:
    return {"status": "alive"}


@app.post("/tasks", status_code=202)
async def submit_task(body: TaskCreate) -> dict:
    task_id = str(uuid.uuid4())
    logger.info("task.received task_id=%s qc_len=%d", task_id, len(body.qc))
    return {"task_id": task_id, "message": "Task submitted successfully."}


@app.get("/tasks/{task_id}")
async def get_task(task_id: str) -> dict:
    raise HTTPException(status_code=501, detail="Not implemented (stub).")
