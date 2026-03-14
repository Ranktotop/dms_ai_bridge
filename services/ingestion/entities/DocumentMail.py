import os
import dataclasses

from shared.helper.HelperConfig import HelperConfig
from services.ingestion.entities.DocumentInterface import DocumentInterface
from services.ingestion.helper.DocumentConverter import DocumentConverter
from services.ingestion.dataclasses import DocMetadata
from shared.clients.llm.LLMClientInterface import LLMClientInterface
from shared.clients.dms.DMSClientInterface import DMSClientInterface
from shared.clients.ocr.OCRClientInterface import OCRClientInterface
from shared.clients.prompt.PromptClientInterface import PromptClientInterface


class DocumentMail(DocumentInterface):
    """
    Implementation for MailIngestion
    """

    def __init__(self,
                 source_file: str,
                 working_directory: str,
                 helper_config: HelperConfig,
                 llm_client: LLMClientInterface,
                 dms_client: DMSClientInterface,
                 file_bytes: bytes | None = None,
                 file_hash: str | None = None,
                 ocr_client: OCRClientInterface | None = None,
                 prompt_client: PromptClientInterface | None = None,
                 forced_tags: list[str] | None = None,
                 forced_document_type: str | None = None,
                 forced_title: str | None = None,
                 forced_correspondent: str | None = None,
                 forced_year: str | None = None,
                 forced_month: str | None = None,
                 forced_day: str | None = None,
                 fallback_title: str | None = None,
                 fallback_correspondent: str | None = None,
                 fallback_year: str | None = None,
                 fallback_month: str | None = None,
                 fallback_day: str | None = None) -> None:
        """
        Initialise a MailDocument

        Args:
            source_file: Absolute path to the source file to ingest.
            working_directory: Temp directory for files
            helper_config: Shared configuration and logger provider.
            llm_client: LLM client for Vision OCR, formatting, metadata, and tag extraction.
            dms_client: DMS client whose cache supplies hints for LLM prompts.
            file_bytes: Optional pre-read file content. Read from disk when not supplied.
            file_hash: Optional precomputed SHA-256 hex digest. Computed automatically when omitted.
            ocr_client: Optional OCR client for Docling-style conversion.
            prompt_client: Optional Prompt client for named template rendering.
            forced_tags: Folder-level tags always merged into the result (never enter LLM hint pool).
            forced_document_type: Folder-level document type that always wins over LLM output.
            forced_title: Title that always wins over LLM output (body: email subject).
            forced_correspondent: Correspondent that always wins over LLM output (body: email sender).
            forced_year: Year that always wins over LLM output (body: from email Date header).
            forced_month: Month that always wins over LLM output (body: from email Date header).
            forced_day: Day that always wins over LLM output (body: from email Date header).
            fallback_title: Title used only when LLM extracted nothing (attachment: "subject — filename").
            fallback_correspondent: Correspondent used only when LLM extracted nothing (attachment: email sender).
            fallback_year: Year used only when LLM extracted nothing (attachment: from email Date header).
            fallback_month: Month used only when LLM extracted nothing (attachment: from email Date header).
            fallback_day: Day used only when LLM extracted nothing (attachment: from email Date header).
        """
        # mail documents have no root_path or path_template — all metadata comes from the LLM
        super().__init__(
            source_file=source_file,
            working_directory=working_directory,
            helper_config=helper_config,
            llm_client=llm_client,
            dms_client=dms_client,
            file_bytes=file_bytes,
            file_hash=file_hash,
            ocr_client=ocr_client,
            prompt_client=prompt_client,
        )
        self._forced_tags = forced_tags or []
        self._forced_document_type = forced_document_type
        # forced fields win over LLM — used for body documents where header values are authoritative
        self._forced_title = forced_title
        self._forced_correspondent = forced_correspondent
        self._forced_year = forced_year
        self._forced_month = forced_month
        self._forced_day = forced_day
        # fallback fields are used only when the LLM extracted nothing — used for attachments
        # where the LLM has first say but the email header provides a meaningful last resort
        self._fallback_title = fallback_title
        self._fallback_correspondent = fallback_correspondent
        self._fallback_year = fallback_year
        self._fallback_month = fallback_month
        self._fallback_day = fallback_day

    ##########################################
    ############# LIFECYCLE ##################
    ##########################################

    def _before_boot(self) -> None:
        pass

    def _before_cleanup(self) -> None:
        pass

    ##########################################
    ############## GETTER ####################
    ##########################################

    def _get_type_name(self) -> str:
        return "File"

    def get_title(self) -> str:
        """
        Return the document title.

        For body documents a forced_title (email subject) is set and returned directly —
        no formatting pass needed. For attachment documents the base class generates a
        formatted title from the LLM-extracted metadata fields, which is preferred over
        the fallback_title (e.g. 'subject — filename').
        """
        if self._forced_title:
            # body documents: email subject is always the correct DMS title
            return self._forced_title
        # attachment documents: LLM-derived formatted title from base class
        return super().get_title()

    ##########################################
    ################ META ####################
    ##########################################

    async def load_metadata(self) -> None:
        # if we have enough forced values we can skip the load from super
        if self._forced_correspondent and self._forced_document_type and  self._forced_year and self._forced_month and self._forced_day and self._forced_title:
            self._metadata_final = DocMetadata(
                correspondent=self._forced_correspondent,
                document_type=self._forced_document_type,
                year=self._forced_year,
                month=self._forced_month,
                day=self._forced_day,
                title=self._forced_title,
                tags=self._forced_tags,
                filename=self._helper_file.get_basename(self._source_file, True),
            )
            self.logging.info("Metadata loaded (forced) for '%s'", self._source_filename, color="green")
            self.logging.debug(self._metadata_final.__dataclass_fields__, color="blue")
            return        
        # if not we run the parent process regulary
        await super().load_metadata()

    def _get_additional_metadata(self) -> DocMetadata:
        # since forced values are never added to additionals we do not add them here
        return DocMetadata(
            filename=self._helper_file.get_basename(self._source_file, True)
        )

    def _get_fallback_metadata(self) -> DocMetadata:
        # we return the fallbacks here
        return DocMetadata(
            correspondent=self._fallback_correspondent,
            filename=self._helper_file.get_basename(self._source_file, True),
            year=self._fallback_year,
            month=self._fallback_month,
            day=self._fallback_day,
            title=self._fallback_title
        )

    def get_tags(self) -> list[str]:
        """
        Return the tag list with forced_tags prepended.

        forced_tags are authoritative (from folder config) and placed first;
        LLM-extracted tags follow — deduplicated by _merge_list_unique.
        """
        # get the regular tags from super
        llm_tags = super().get_tags()
        # forced_tags are authoritative (from folder config) and placed first;
        # LLM tags follow — deduplicated by _merge_list_unique
        return self._merge_list_unique(self._forced_tags, llm_tags)

    def get_metadata(self) -> DocMetadata:
        """
        Return metadata with forced_document_type applied on top of the LLM result.

        forced_document_type wins over the LLM value when set. A new DocMetadata
        instance is returned via dataclasses.replace to avoid mutating the cached
        _metadata_final which may be read again later.
        """
        meta = super().get_metadata()
        if not self._forced_document_type:
            return meta
        # forced_document_type wins over the LLM value — create a new instance to avoid
        # mutating the cached _metadata_final which may be read again later
        return dataclasses.replace(meta, document_type=self._forced_document_type)
