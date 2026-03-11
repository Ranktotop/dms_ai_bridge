"""Core orchestrator for the document ingestion pipeline."""
import hashlib

from shared.clients.cache.CacheClientInterface import CacheClientInterface, KEY_INGESTION_FILE
from shared.clients.dms.DMSClientInterface import DMSClientInterface
from shared.clients.dms.models.DocumentUpdate import DocumentUpdateRequest
from shared.clients.llm.LLMClientInterface import LLMClientInterface
from shared.clients.ocr.OCRClientInterface import OCRClientInterface
from shared.clients.prompt.PromptClientInterface import PromptClientInterface
from shared.helper.HelperConfig import HelperConfig
from services.doc_ingestion.helper.Document import Document
from services.doc_ingestion.Exceptions import DocumentValidationError, DocumentPathValidationError
from shared.helper.HelperFile import HelperFile

class IngestionService:
    """Orchestrates the file ingestion pipeline:
    path parse -> (convert to PDF) -> OCR -> metadata -> DMS upload -> DMS update.
    """

    def __init__(
        self,
        helper_config: HelperConfig,
        dms_client: DMSClientInterface,
        llm_client: LLMClientInterface,
        cache_client: CacheClientInterface,
        template: str | None = None,
        default_owner_id: int | None = None,
        ocr_client: OCRClientInterface | None = None,
        prompt_client: PromptClientInterface|None = None
    ) -> None:
        self._config = helper_config
        self.logging = helper_config.get_logger()
        self._llm_client = llm_client
        self._dms_client = dms_client
        self._cache_client = cache_client
        self._template = template
        self._default_owner_id = default_owner_id
        self._ocr_client = ocr_client
        self._prompt_client = prompt_client

        self._helper_file = HelperFile()

    ##########################################
    ############# INGESTION ##################
    ##########################################

    async def do_ingest_files_batch(self, file_paths: list[str], root_path: str, batch_size: int = 0) -> None:
        """Ingest multiple files using a phased batch approach.

        Processes files in sub-batches so each LLM model stays loaded for the
        full sub-batch before being swapped out:

          Phase 1 — Vision LLM: ``boot_extract()`` for every file.
          Phase 2 — Chat LLM:   ``boot_chat()``    for every extracted file.
          Phase 3 — Upload:     DMS upload + metadata update for every analysed file.

        Args:
            file_paths: Ordered list of absolute file paths to ingest.
            root_path:  Root scan directory (used for relative path calculation).
            batch_size: Maximum files per sub-batch.  ``0`` means no limit
                        (all files processed in a single batch).
        """
        if not file_paths:
            return
        # split files into batches
        document_batches = (
            [file_paths[i:i + batch_size] for i in range(0, len(file_paths), batch_size)]
            if batch_size > 0
            else [file_paths]
        )
        for batch in document_batches:
            await self._ingest_batch(batch, root_path)

    async def _ingest_batch(self, file_paths: list[str], root_path: str) -> None:
        """Run one complete batch for the given file paths.

        Order of operations:
          1. Boot (path parse, format check) — cheap, no I/O.
          2. Upload to DMS — early gate: duplicate or rejected files are skipped
             immediately, before any expensive OCR or LLM work is done.
          3. Load content (OCR / Vision LLM).
          4. Format content.
          5. Load metadata (LLM chat).
          6. Load tags (LLM chat).
          7. Update DMS document with full metadata.

        If any step after the initial upload fails, the already-uploaded document is
        deleted from the DMS and its cache entry is removed (rollback).
        """

        # ── Step 0: hash check + boot ──────────────────────────────────────────
        booted: list[tuple[Document, str]] = []  # (doc, cache_key)

        for file_path in file_paths:
            with open(file_path, "rb") as f:
                file_bytes = f.read()
            file_hash = hashlib.sha256(file_bytes).hexdigest()
            cache_key = "%s:%s" % (KEY_INGESTION_FILE, file_hash)
            cached_doc_id = await self._cache_client.do_get(cache_key)
            if cached_doc_id is not None:
                self.logging.info("Skipping '%s': already ingested (doc_id=%s).", file_path, cached_doc_id, color="blue")
                continue

            doc = Document(
                root_path=root_path,
                source_file=file_path,
                working_directory=self._helper_file.generate_tempfolder(path_only=True),
                helper_config=self._config,
                llm_client=self._llm_client,
                dms_client=self._dms_client,
                path_template=self._template,
                file_bytes=file_bytes,
                file_hash=file_hash,
                ocr_client=self._ocr_client,
                prompt_client=self._prompt_client,
            )
            try:
                doc.boot()
                booted.append((doc, cache_key))
            except DocumentPathValidationError as e:
                self.logging.warning("Skipping '%s': %s", file_path, e, color="yellow")
            except DocumentValidationError as e:
                self.logging.error("Skipping '%s': %s", file_path, e, color="red")
            except Exception as e:
                self.logging.error("Failed to boot document '%s': %s", file_path, e)

        # ── Step 1: upload — early gate before any expensive processing ────────
        uploaded: list[tuple[Document, int, str]] = []  # (doc, doc_id, cache_key)
        for doc, cache_key in booted:
            doc_id = await self._upload_document(doc, cache_key)
            if doc_id is None:
                doc.cleanup()
                continue
            uploaded.append((doc, doc_id, cache_key))

        # ── Phase 1: load content ──────────────────────────────────────────────
        content_docs: list[tuple[Document, int, str]] = []
        for doc, doc_id, cache_key in uploaded:
            try:
                await doc.load_content()
                content_docs.append((doc, doc_id, cache_key))
            except Exception as e:
                self.logging.error("Failed to load content for '%s': %s", doc.get_source_file(True), e)
                await self._rollback_document(doc_id, cache_key)
                doc.cleanup()

        # ── Phase 2: format content ────────────────────────────────────────────
        formatted_docs: list[tuple[Document, int, str]] = []
        for doc, doc_id, cache_key in content_docs:
            try:
                await doc.format_content()
                formatted_docs.append((doc, doc_id, cache_key))
            except Exception as e:
                self.logging.error("Failed to format content for '%s': %s", doc.get_source_file(True), e)
                await self._rollback_document(doc_id, cache_key)
                doc.cleanup()

        # ── Phase 3: load metadata ─────────────────────────────────────────────
        meta_docs: list[tuple[Document, int, str]] = []
        new_document_types: list[str] = []
        new_document_types_lc: list[str] = []
        new_correspondents: list[str] = []
        new_correspondents_lc: list[str] = []
        new_tags: list[str] = []
        new_tags_lc: list[str] = []

        for doc, doc_id, cache_key in formatted_docs:
            try:
                await doc.load_metadata(
                    additional_doc_types=new_document_types,
                    additional_correspondents=new_correspondents,
                    additional_tags=new_tags)
                meta_docs.append((doc, doc_id, cache_key))

                # Accumulate hints for subsequent documents in the batch
                meta = doc.get_metadata()
                if meta.document_type and meta.document_type.lower() not in new_document_types_lc:
                    new_document_types.append(meta.document_type)
                    new_document_types_lc.append(meta.document_type.lower())
                if meta.correspondent and meta.correspondent.lower() not in new_correspondents_lc:
                    new_correspondents.append(meta.correspondent)
                    new_correspondents_lc.append(meta.correspondent.lower())
                for tag in (meta.tags or []):
                    if tag.lower() not in new_tags_lc:
                        new_tags.append(tag)
                        new_tags_lc.append(tag.lower())
            except Exception as e:
                self.logging.error("Failed to load metadata for '%s': %s", doc.get_source_file(True), e)
                await self._rollback_document(doc_id, cache_key)
                doc.cleanup()

        # ── Phase 4: load tags ─────────────────────────────────────────────────
        tagged_docs: list[tuple[Document, int, str]] = []
        for doc, doc_id, cache_key in meta_docs:
            try:
                await doc.load_tags(additional_tags=new_tags)
                tagged_docs.append((doc, doc_id, cache_key))
                for tag in doc.get_tags():
                    if tag.lower() not in new_tags_lc:
                        new_tags.append(tag)
                        new_tags_lc.append(tag.lower())
            except Exception as e:
                self.logging.error("Failed to load tags for '%s': %s", doc.get_source_file(True), e)
                await self._rollback_document(doc_id, cache_key)
                doc.cleanup()

        # ── Phase 5: update DMS metadata ──────────────────────────────────────
        for doc, doc_id, cache_key in tagged_docs:
            try:
                await self._update_document(doc_id, doc)
            except Exception as e:
                self.logging.error("Failed to update document id=%d ('%s'): %s", doc_id, doc.get_source_file(True), e)
                await self._rollback_document(doc_id, cache_key)
            finally:
                doc.cleanup()

    ##########################################
    ################# DMS ####################
    ##########################################

    async def _rollback_document(self, doc_id: int, cache_key: str) -> None:
        """Delete an already-uploaded DMS document and remove its cache entry.

        Called when any pipeline phase after the initial upload fails, so the
        partially-processed document does not remain in the DMS.
        """
        try:
            await self._dms_client.do_delete_document(doc_id)
            self.logging.warning("Rolled back DMS document id=%d.", doc_id, color="yellow")
        except Exception as e:
            self.logging.error("Rollback: failed to delete DMS document id=%d: %s", doc_id, e)
        try:
            await self._cache_client.do_delete(cache_key)
        except Exception as e:
            self.logging.error("Rollback: failed to remove cache entry '%s': %s", cache_key, e)

    async def _upload_document(self, document: Document, cache_key: str) -> int | None:
        """
        Uploads the given document to dms and saves it to the cache

        Args:
            document: The Document instance to upload.
            cache_key: The cache key under which to store the document ID.

        Returns:
            The DMS document ID if the upload was successful, or None if the document was skipped (e.g., due to duplication) or if an error occurred.
        """
        # Upload original file to dms (file_bytes already read above for hash check)        
        # Get the required data
        file_name = document.get_source_file(filename_only=True)
        file_path = document.get_source_file()
        file_bytes = document.get_file_bytes()
        try:
            doc_id = await self._dms_client.do_upload_document(
                file_bytes=file_bytes,
                file_name=file_name,
                owner_id=self._default_owner_id,
            )
        except FileExistsError as e:
            dup_id: int | None = e.args[0] if e.args else None
            if dup_id is not None:
                self.logging.warning(
                    "Skipping '%s': duplicate of DMS doc id=%d. Caching hash.",
                    file_path, dup_id, color="yellow",
                )
                await self._cache_client.do_set(cache_key, str(dup_id))
            else:
                self.logging.warning("Skipping '%s': already exists in DMS.", file_path)
            return None
        except Exception as e:
            self.logging.error("Upload failed for '%s': %s", file_path, e)
            return None        

        # Store hash after confirmed upload so the file is skipped on future runs
        await self._cache_client.do_set(cache_key, str(doc_id))        
        return doc_id

    async def _update_document(self, dms_doc_id: int, document: Document) -> None:
        """
        Update a previously uploaded DMS document with full extracted metadata.

        Args:
            dms_doc_id: The DMS document ID obtained from the initial upload step.
            document: A Document instance that has completed all analysis phases.

        Raises:
            Exception: Any error encountered while resolving DMS entities or
                patching the document — callers must handle this for rollback.
        """
        file_path = document.get_source_file()
        meta = document.get_metadata()
        tags = document.get_tags()
        title = document.get_title()
        content = document.get_content()
        date_string = document.get_date_string(pattern="%Y-%m-%d")

        # Resolve/create DMS entities — let exceptions propagate to the caller
        correspondent_id: int | None = None
        document_type_id: int | None = None
        tag_ids: list[int] = []

        if meta.correspondent:
            try:
                correspondent_id = await self._dms_client.do_resolve_or_create_correspondent(meta.correspondent)
            except Exception as e:
                self.logging.warning("Failed to resolve correspondent '%s': %s", meta.correspondent, e)
                raise

        if meta.document_type:
            try:
                document_type_id = await self._dms_client.do_resolve_or_create_document_type(meta.document_type)
            except Exception as e:
                self.logging.warning("Failed to resolve document_type '%s': %s", meta.document_type, e)
                raise

        for tag_name in tags:
            try:
                tag_id = await self._dms_client.do_resolve_or_create_tag(tag_name)
                tag_ids.append(tag_id)
            except Exception as e:
                self.logging.warning("Failed to resolve tag '%s': %s", tag_name, e)
                raise

        await self._dms_client.do_update_document(
            document_id=dms_doc_id,
            update=DocumentUpdateRequest(
                title=title,
                correspondent_id=correspondent_id,
                document_type_id=document_type_id,
                tag_ids=tag_ids,
                content=content,
                created_date=date_string,
                owner_id=self._default_owner_id,
            ),
        )

        self.logging.info(
            "File '%s' ingested successfully -> DMS document id=%d", file_path, dms_doc_id
        )