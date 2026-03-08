#!/bin/bash
set -e
export PATH="/opt/venv/bin:$PATH"
export PYTHONPATH="/app"
exec python3 -u /app/services/dms_rag_sync/dms_rag_sync.py
