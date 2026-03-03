# OpenWebUI Pipeline

Connects OpenWebUI to dms_ai_bridge for document-aware chat.

## Installation

1. Copy `pipeline.py` to your OpenWebUI `pipelines/` directory
2. Restart OpenWebUI
3. Go to Admin Panel -> Pipelines -> configure the Valves

## Valves (Configuration)

| Valve | Default | Description |
|-------|---------|-------------|
| BASE_URL | `http://dms-bridge:8000` | dms_ai_bridge server URL |
| API_KEY | `` | X-Api-Key header value |
| USER_ID | `5` | OpenWebUI user ID (must be in config/user_mapping.yml) |
| LIMIT | `5` | Max search results passed to agent |
| STREAM | `true` | Enable SSE streaming (recommended) |
| TIMEOUT | `60.0` | HTTP timeout in seconds |

## Usage

Select "DMS AI Bridge" as the model in a chat and ask questions about your documents.

## Endpoint

Calls `POST /chat/openwebui/stream` (STREAM=true) or `POST /chat/openwebui` (STREAM=false).
Requires `X-Api-Key` header and `user_id` mapped in `config/user_mapping.yml`.
