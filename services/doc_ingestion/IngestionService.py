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
        """Run one complete three-phase batch for the given file paths."""

        # boot each document if not already in cache. If boot fails ignore the document
        booted_docs: list[Document] = []

        for file_path in file_paths:            
            # check file hash cache — skip immediately if already ingested
            with open(file_path, "rb") as f:
                file_bytes = f.read()
            file_hash = hashlib.sha256(file_bytes).hexdigest()
            cache_key = "%s:%s" % (KEY_INGESTION_FILE, file_hash)
            cached_doc_id = await self._cache_client.do_get(cache_key)
            if cached_doc_id is not None:
                self.logging.info("Skipping '%s': already ingested (doc_id=%s).", file_path, cached_doc_id, color="blue")
                continue

            # init the document
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
            # boot the document
            try:
                doc.boot()
                booted_docs.append(doc)
            except DocumentPathValidationError as e:
                self.logging.warning("Skipping '%s': %s", file_path, e, color="yellow")
            except DocumentValidationError as e:
                self.logging.error("Skipping '%s': %s", file_path, e, color="red")
            except Exception as e:
                self.logging.error("Failed to boot document '%s': %s", file_path, e)
        
        # Phase 1: load the content for all files
        docs_with_content: list[Document] = []
        for doc in booted_docs:
            try:
                await doc.load_content()
                docs_with_content.append(doc)
            except Exception as e:
                self.logging.error("Failed to load content for document '%s': %s", doc.get_source_file(True), e)
                doc.cleanup()

        # Phase 2: Format the content if needed
        formatted_docs: list[Document] = []
        for doc in docs_with_content:
            try:
                await doc.format_content()
                formatted_docs.append(doc)
            except Exception as e:
                self.logging.error("Failed to format content for document '%s': %s", doc.get_source_file(True), e)
                doc.cleanup()

        # Phase 3: Load the meta
        meta_docs: list[Document] = []
        new_document_types = []
        new_document_types_lc = []
        new_correspondents = []
        new_correspondents_lc = []
        new_tags = []
        new_tags_lc = []

        for doc in formatted_docs:
            try:
                await doc.load_metadata(
                    additional_doc_types=new_document_types,
                    additional_correspondents=new_correspondents,
                    additional_tags=new_tags)
                meta_docs.append(doc)

                # Collect the document types, correspondents and tags for the batch so they can be included as hints in the prompt for the next documents in the batch
                meta = doc.get_metadata()
                #doc types
                chosen_doc_type = meta.document_type
                if chosen_doc_type and chosen_doc_type.lower() not in new_document_types_lc:
                    new_document_types.append(chosen_doc_type)
                    new_document_types_lc.append(chosen_doc_type.lower())
                # correspondent
                chosen_correspondent = meta.correspondent
                if chosen_correspondent and chosen_correspondent.lower() not in new_correspondents_lc:
                    new_correspondents.append(chosen_correspondent)
                    new_correspondents_lc.append(chosen_correspondent.lower())
                # tags
                chosen_tags = meta.tags if meta.tags else []
                for tag in chosen_tags:
                    if tag.lower() not in new_tags_lc:
                        new_tags.append(tag)
                        new_tags_lc.append(tag.lower())

            except Exception as e:
                self.logging.error("Failed to load metadata for document '%s': %s", doc.get_source_file(True), e)
                doc.cleanup()

        # Phase 4: Collect the tags
        tagged_docs: list[Document] = []
        for doc in meta_docs:
            try:
                await doc.load_tags(additional_tags=new_tags)
                tagged_docs.append(doc)
                #add the new tags to the list for the next documents in the batch so they are included as hints in the prompt
                chosen_tags = doc.get_tags()
                for tag in chosen_tags:
                    if tag.lower() not in new_tags_lc:
                        new_tags.append(tag)
                        new_tags_lc.append(tag.lower())
            except Exception as e:
                self.logging.error("Failed to load tags for document '%s': %s", doc.get_source_file(True), e)
                doc.cleanup()

        # Phase 5: Upload to DMS
        for doc in tagged_docs:
            try:
                await self._push_document(doc, "%s:%s" % (KEY_INGESTION_FILE, doc.get_file_hash()))
            finally:
                doc.cleanup()

    ##########################################
    ################# DMS ####################
    ##########################################

    async def _push_document(self, document: Document, cache_key: str) -> int | None:
        """
        Pushes a fully booted and analysed Document through the final upload and metadata update steps.

        Args:
            document: A Document instance that has completed all boot and analysis phases.
            cache_key: The cache key corresponding to this document's file hash, used for caching the DMS document ID.        
        """
        # Get the required data
        file_name = document.get_source_file(filename_only=True)
        file_path = document.get_source_file()
        file_bytes = document.get_file_bytes()
        meta = document.get_metadata()
        tags = document.get_tags()
        title = document.get_title()
        content = document.get_content()
        date_string = document.get_date_string(pattern="%Y-%m-%d") # e.g. "2024-06-30"

        # Resolve/create DMS entities
        correspondent_id: int | None = None
        document_type_id: int | None = None
        tag_ids: list[int] = []

        if meta.correspondent:
            try:
                correspondent_id = await self._dms_client.do_resolve_or_create_correspondent(meta.correspondent)
            except Exception as e:
                self.logging.warning("Failed to resolve correspondent '%s': %s", meta.correspondent, e)

        if meta.document_type:
            try:
                document_type_id = await self._dms_client.do_resolve_or_create_document_type(meta.document_type)
            except Exception as e:
                self.logging.warning("Failed to resolve document_type '%s': %s", meta.document_type, e)

        for tag_name in tags:
            try:
                tag_id = await self._dms_client.do_resolve_or_create_tag(tag_name)
                tag_ids.append(tag_id)
            except Exception as e:
                self.logging.warning("Failed to resolve tag '%s': %s", tag_name, e)

        # Upload original file to dms (file_bytes already read above for hash check)
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

        # Upsert the DMS Document with the extracted metadata
        try:
            await self._dms_client.do_update_document(
                document_id=doc_id,
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
        except Exception as e:
            self.logging.error(
                "Metadata update failed for document id=%d ('%s'): %s",
                doc_id, file_path, e,
            )

        self.logging.info(
            "File '%s' ingested successfully -> DMS document id=%d", file_path, doc_id
        )
        return doc_id