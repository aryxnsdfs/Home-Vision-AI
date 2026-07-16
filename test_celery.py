import os
import json
import uuid
import asyncio
import redis.asyncio as aioredis
from celery_worker import generate_architecture_task

async def test():
    req = {
        "prompt": "add a bathroom",
        "currentProject": {
            "rooms": [
                {"id": "bedroom-1", "type": "bedroom", "name": "Bedroom", "x": 0, "z": 0, "width": 10, "length": 10}
            ]
        }
    }
    job_id = str(uuid.uuid4())
    print(f"Queuing job {job_id}")
    
    generate_architecture_task.delay(req, job_id)
    
    redis_client = aioredis.from_url("redis://localhost:6379/0")
    pubsub = redis_client.pubsub()
    await pubsub.subscribe(job_id)
    
    print("Waiting for messages...")
    try:
        async for message in pubsub.listen():
            if message['type'] == 'message':
                data = message['data'].decode('utf-8')
                print("GOT:", data)
                msg_dict = json.loads(data)
                if msg_dict.get('done') or msg_dict.get('error'):
                    break
    except Exception as e:
        print("Error:", e)
    finally:
        await pubsub.unsubscribe()
        await pubsub.close()

if __name__ == "__main__":
    asyncio.run(test())
