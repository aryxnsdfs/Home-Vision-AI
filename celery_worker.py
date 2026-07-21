import os
import json
import logging
from dotenv import load_dotenv
load_dotenv()  # Load API keys from .env file

from celery import Celery
import redis

# Force server.py dependencies to load without initializing the FastAPI app serving loop
from server import _stream_generate_work, _stream_template_work, GenerateRequest, TemplateRequest

logger = logging.getLogger("homevision_worker")

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# Initialize Celery
app = Celery("homevision_worker", broker=REDIS_URL, backend=REDIS_URL)

# Initialize Redis client for Pub/Sub
redis_client = redis.Redis.from_url(REDIS_URL)

@app.task(name="generate_architecture")
def generate_architecture_task(request_data: dict, job_id: str):
    logger.info(f"[WORKER] Starting architecture job {job_id}")
    
    def emit_fn(msg_dict: dict):
        redis_client.publish(job_id, json.dumps(msg_dict))
        
    try:
        req = GenerateRequest(**request_data)
        _stream_generate_work(req, emit_fn)
    except Exception as e:
        logger.error(f"[WORKER] Job {job_id} failed: {e}")
        emit_fn({
            "success": False,
            "status": "generation_failed",
            "error_code": "INVALID_LAYOUT",
            "message": str(e)
        })
        raise
@app.task(name="generate_template")
def generate_template_task(request_data: dict, job_id: str):
    logger.info(f"[WORKER] Starting template job {job_id}")
    
    def emit_fn(msg_dict: dict):
        redis_client.publish(job_id, json.dumps(msg_dict))
        
    try:
        req = TemplateRequest(**request_data)
        _stream_template_work(req, emit_fn)
    except Exception as e:
        logger.error(f"[WORKER] Job {job_id} failed: {e}")
        emit_fn({
            "success": False,
            "status": "generation_failed",
            "error_code": "INVALID_LAYOUT",
            "message": str(e)
        })
        raise
