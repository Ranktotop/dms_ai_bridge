"""Core orchestrator for the document ingestion pipeline."""
import os

from shared.clients.dms.DMSClientInterface import DMSClientInterface
from shared.clients.dms.models.DocumentUpdate import DocumentUpdateRequest
from shared.clients.llm.LLMClientInterface import LLMClientInterface
from shared.helper.HelperConfig import HelperConfig
from services.doc_ingestion.helper.Document import Document
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
        template: str | None = None,
        default_owner_id: int | None = None
    ) -> None:
        self._config = helper_config
        self.logging = helper_config.get_logger()
        self._llm_client = llm_client
        self._dms_client = dms_client
        self._template = template
        self._default_owner_id = default_owner_id
        self._helper_file = HelperFile()

    ##########################################
    ############### CORE #####################
    ##########################################

    async def do_ingest_file(self, file_path: str, root_path: str) -> int | None:
        """Ingest a single file into the DMS using a two-step upload + PATCH approach.

        Steps:
        1. Parse path template -> ParsedPathMetadata
        2. Convert to PDF if needed (DocHelper) — original file is preserved for upload
        3. Text/OCR extraction on the (possibly converted) PDF
        4. Fill missing metadata via MetadataExtractor.do_extract_metadata()
        5. Extract tags via MetadataExtractor.do_extract_tags()
        6. Build title from formula: '{Correspondent} {DocType} {yyyy.mm.dd}'
        7. Resolve/create correspondent, document_type, tags in DMS
        8. Upload original file bytes (minimal — only file + owner_id)
        9. PATCH document with full metadata (wins over Paperless OCR results)

        Args:
            file_path: Absolute path to the file.
            root_path: Root scan directory (used for relative path calculation).

        Returns:
            DMS document ID on success, None on failure.
        """
        self.logging.info("Ingesting file '%s'...", file_path)

        #load dms cache first
        await self._dms_client.fill_cache()

        # Load the file as document
        theDocument = Document(
            root_path=root_path,
            source_file=file_path,
            working_directory=self._helper_file.generate_tempfolder(path_only=True),
            helper_config=self._config,
            llm_client=self._llm_client,
            dms_client=self._dms_client,
            path_template=self._template
        )
        # Boot up the document (fetches text of the file)
        try:
            await theDocument.boot()
        except Exception as e:
            self.logging.error("Failed to boot document '%s': %s", file_path, e)
            return None
        
        # Ingest the document into the DMS. Cleanup afterwards to remove temp files.
        try:
            # Get the required data
            meta = theDocument.get_metadata()
            tags = theDocument.get_tags()
            title = theDocument.get_title()
            content = theDocument.get_content()
            date_string = theDocument.get_date_string(pattern="%Y-%m-%d") # e.g. "2024-06-30"

            # Resolve/create DMS entities
            correspondent_id: int | None = None
            document_type_id: int | None = None
            tag_ids: list[int] = []

            if meta.correspondent:
                try:
                    correspondent_id = await self._dms_client.do_resolve_or_create_correspondent(meta.correspondent)
                except Exception as exc:
                    self.logging.warning("Failed to resolve correspondent '%s': %s", meta.correspondent, exc)

            if meta.document_type:
                try:
                    document_type_id = await self._dms_client.do_resolve_or_create_document_type(meta.document_type)
                except Exception as exc:
                    self.logging.warning("Failed to resolve document_type '%s': %s", meta.document_type, exc)

            for tag_name in tags:
                try:
                    tag_id = await self._dms_client.do_resolve_or_create_tag(tag_name)
                    tag_ids.append(tag_id)
                except Exception as exc:
                    self.logging.warning("Failed to resolve tag '%s': %s", tag_name, exc)

            # Upload original file to dms
            try:
                with open(file_path, "rb") as f:
                    file_bytes = f.read()

                file_name = os.path.basename(file_path)

                doc_id = await self._dms_client.do_upload_document(
                    file_bytes=file_bytes,
                    file_name=file_name,
                    owner_id=self._default_owner_id,
                )
            except FileExistsError:
                self.logging.warning("Skipping '%s': already exists in DMS.", file_path)
                return None
            except Exception as exc:
                self.logging.error("Upload failed for '%s': %s", file_path, exc)
                return None

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
            except Exception as exc:
                self.logging.error(
                    "Metadata update failed for document id=%d ('%s'): %s",
                    doc_id, file_path, exc,
                )

            self.logging.info(
                "File '%s' ingested successfully -> DMS document id=%d", file_path, doc_id
            )
            return doc_id
        finally:
            theDocument.cleanup()
