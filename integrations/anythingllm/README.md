# AnythingLLM Agent Skill

Connects AnythingLLM agents to dms_ai_bridge for document search.

## Installation

1. Copy `dms_bridge_skill.js` to the AnythingLLM skills directory
2. Restart AnythingLLM
3. Go to Agent Settings and enable the "DMS Document Search" skill

## Configuration

Edit the `settings` block in `dms_bridge_skill.js` or configure via the AnythingLLM admin UI:

| Setting | Default | Description |
|---------|---------|-------------|
| API_URL | `http://dms-bridge:8000` | dms_ai_bridge server URL |
| API_KEY | `` | X-Api-Key header value |
| USER_ID | `1` | AnythingLLM user ID (must be in config/user_mapping.yml) |
| LIMIT | `5` | Max search results |
| TIMEOUT_MS | `30000` | Request timeout in ms |

## Usage

In a workspace with Agent mode enabled:

> "Search my documents for invoices from last year"
> "Find the contract with Acme Corp"
> "Show me all receipts over 100 euros"

## Endpoint Fallback

The skill tries `POST /chat/anythingllm` first (Phase IV — returns synthesised answer).
If unavailable, it falls back to `POST /query/anythingllm` (Phase III — formats raw search results).
