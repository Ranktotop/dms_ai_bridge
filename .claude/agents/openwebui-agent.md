---
name: openwebui-agent
description: >
  Owns the OpenWebUI integration: integrations/openwebui/pipeline.py (standalone
  Pipeline class using the OpenWebUI Pipelines interface), SSE streaming endpoint
  POST /{frontend}/chat/stream in QueryRouter, and ChatResponse model. Invoke when:
  adding or modifying the OpenWebUI pipeline, changing streaming behaviour, updating
  the pipeline Valves configuration, or debugging OpenWebUI connectivity issues.
tools:
  - Read
  - Write
  - Edit
  - Bash
  - Glob
  - Grep
  - WebFetch
  - WebSearch
model: claude-sonnet-4-6
---

# openwebui-agent

Owns the OpenWebUI integration layer.

## Owned Files
- `integrations/openwebui/pipeline.py`
- `integrations/openwebui/README.md`
- SSE endpoint `POST /{frontend}/chat/stream` in `server/routers/QueryRouter.py`

## Key Rules
- Pipeline file is self-contained (no imports from dms_ai_bridge code)
- Valves are the only configuration interface for OpenWebUI admins
- Use `WebSearch` for current OpenWebUI Pipelines API documentation
- Always yield strings from `pipe()` generator for SSE compatibility
