#!/bin/bash
echo "Starting Celery with auto-reload for Python files..."
watchfiles --filter python "python3 -m celery -A celery_worker worker --loglevel=info --pool=solo" .
