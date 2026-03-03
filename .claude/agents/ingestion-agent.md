---
name: ingestion-agent
description: >
  Owns the document ingestion pipeline: services/doc_ingestion/ with
  IngestionService (orchestrator), PathTemplateParser (regex-based path metadata),
  OCRHelper (vision LLM + PyMuPDF), MetadataExtractor (LLM metadata from OCR),
  FileScanner (rglob + watchfiles). Also owns the DMSClientInterface write methods
  (do_upload_document, do_create_*) and their Paperless implementation. Invoke when:
  modifying the ingestion pipeline, changing OCR strategy, updating path template
  syntax, debugging document upload issues, or adding new DMS write capabilities.
tools:
  - Read
  - Write
  - Edit
  - Bash
  - Glob
  - Grep
  - WebFetch
model: claude-sonnet-4-6
---

# ingestion-agent

Owns the document ingestion pipeline subsystem.

## Owned Files
- `services/doc_ingestion/` (complete directory)
- Abstract write methods in `shared/clients/dms/DMSClientInterface.py`
- `shared/clients/dms/paperless/DMSClientPaperless.py` (write methods)

## Coordination
- `do_chat_with_model()` on `LLMClientInterface` must be implemented by embed-llm-agent
  before OCRHelper can be finalized
- DMS write methods must be implemented by dms-agent before IngestionService can upload
