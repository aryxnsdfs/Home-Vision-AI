#!/bin/bash
echo "Starting Celery with auto-reload (watchfiles)..."
watchfiles "python3 -m celery -A celery_worker worker --loglevel=info --pool=solo" .
