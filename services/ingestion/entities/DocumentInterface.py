from abc import ABC, abstractmethod

from shared.helper.HelperConfig import HelperConfig
from services.ingestion.helper.DocumentConverter import DocumentConverter
from services.ingestion.dataclasses import DocMetadata
from shared.clients.llm.LLMClientInterface import LLMClientInterface
from shared.clients.dms.DMSClientInterface import DMSClientInterface
from shared.clients.ocr.OCRClientInterface import OCRClientInterface
from shared.clients.prompt.PromptClientInterface import PromptClientInterface
from shared.clients.prompt.models.Prompt import PromptConfig, PromptConfigMessage
from typing import Union
import os
import datetime
from uuid import uuid4
from shared.helper.HelperFile import HelperFile
from collections import Counter
import fitz  # PyMuPDF
import base64
import re
import hashlib


class DocumentInterface(ABC):
    """
    Interface for Ingestion Entities
    
    Lifecycle:
        1. Instantiate the concrete subclass with source file path and dependencies.
        2. Call ``boot()`` — sets up the working directory, converts the file.
        3. Call ``await load_content()`` — extracts raw page text.
        4. Call ``await format_content()`` — formats/merges pages into final Markdown.
        5. Call ``await load_metadata()`` — merges path + LLM metadata.
        6. Call ``await load_tags()`` — extracts tags via LLM.
        7. Call ``cleanup()`` in a ``finally`` block to remove temporary files.
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
                 prompt_client: PromptClientInterface | None = None) -> None:
        """
        Initialise the Interface

        Args:
            source_file (str): Absolute path to the source file to ingest.
            working_directory (str): Temp directory for files.
            helper_config (HelperConfig): Shared configuration and logger provider.
            llm_client (LLMClientInterface): LLM client for Vision OCR, formatting, metadata, and tag extraction.
            dms_client (DMSClientInterface): DMS client whose cache supplies hints for LLM prompts.
            file_bytes (bytes | None): Optional pre-read file content. Read from disk when not supplied.
            file_hash (str | None): Optional precomputed SHA-256 hex digest. Computed automatically when omitted.
            ocr_client (OCRClientInterface | None): Optional OCR client for Docling-style conversion.
            prompt_client (PromptClientInterface | None): Optional Prompt client for named template rendering.
        """
        # general
        self.logging = helper_config.get_logger()
        self._language = helper_config.get_string_val("LANGUAGE", "German")
        self._page_dpi = helper_config.get_number_val(f"{self._get_type_name().upper()}_INGESTION_PAGE_DPI", 150)
        self._minimum_text_chars_for_direct_read = helper_config.get_number_val(f"{self._get_type_name().upper()}_INGESTION_MINIMUM_TEXT_CHARS_FOR_DIRECT_READ", 40)

        # owner specific
        self._owner_company_name = helper_config.get_string_val(f"{self._get_type_name().upper()}_INGESTION_COMPANY_NAME", None)

        # content-loading feature flags — allow individual reading strategies
        # to be disabled via environment config for debugging or performance tuning
        self._skip_direct_read = helper_config.get_bool_val(f"{self._get_type_name().upper()}_INGESTION_SKIP_DIRECT_READ", False)
        self._skip_programmatic_read = helper_config.get_bool_val(f"{self._get_type_name().upper()}_INGESTION_SKIP_PROGRAMMATIC_READ", False)
        self._skip_vision_read = helper_config.get_bool_val(f"{self._get_type_name().upper()}_INGESTION_SKIP_VISION_READ", False)
        self._skip_ocr_read = helper_config.get_bool_val(f"{self._get_type_name().upper()}_INGESTION_SKIP_OCR_READ", False)

        # files and paths
        self._source_file = source_file
        # embed a short UUID fragment in the working dir name to prevent collisions
        # when multiple documents are booted concurrently in the same base directory
        self._working_directory = os.path.join(working_directory, str(uuid4().hex[:8]))

        # read file bytes eagerly so the caller does not need to keep the file open
        if not file_bytes:
            with open(source_file, "rb") as f:
                self._file_bytes = f.read()
        else:
            self._file_bytes = file_bytes

        # compute hash lazily only when not pre-supplied (e.g. already hashed by the service)
        if not file_hash:
            self._original_file_hash = hashlib.sha256(self._file_bytes).hexdigest()
        else:
            self._original_file_hash = file_hash

        # helper
        self._helper_config = helper_config
        self._helper_file = HelperFile()

        # clients
        self._llm_client = llm_client
        self._dms_client = dms_client
        self._ocr_client = ocr_client
        self._prompt_client = prompt_client

        # bootable state — all fields below are None until boot() succeeds
        # helper
        self._converter: DocumentConverter | None = None

        # file
        self._converted_file: str | None = None
        self._source_filename: str | None = None
        self._source_extension: str | None = None
        self._converted_extension: str | None = None

        # content
        self._content_needs_formatting: bool | None = None
        self._page_contents: list[str] | None = None
        self._final_content: str | None = None

        # metadata
        self._metadata_final: DocMetadata | None = None
        self._tags: list[str] | None = None

        # additional
        self._additional_tags: list[str] = []
        self._additional_correspondents: list[str] = []
        self._additional_doc_types: list[str] = []

    ##########################################
    ############# LIFECYCLE ##################
    ##########################################

    @abstractmethod
    def _before_boot(self) -> None:
        """
        Runs before the regular boot process

        Raises:
            Exception: If boot fails
        """

    def boot(self) -> None:
        """
        Boot the document: run subclass pre-boot checks, create working directory, and convert the source file.

        Raises:
            RuntimeError: If the working directory cannot be created, LLM_MODEL_VISION is not configured, or conversion fails.
        """
        # run the subclass-specific pre-boot checks and setup
        self._before_boot()

        # create the UUID-named working directory for temporary conversion output
        if not self._helper_file.create_folder(self._working_directory):
            raise RuntimeError("Failed to create working directory for %sDocument in '%s'" % (self._get_type_name(), self._working_directory))

        # wrap remaining setup so cleanup() is called on any error — temp dirs
        # must not be left behind if the converter initialisation fails
        try:
            # Vision LLM is required for all document types except plain text;
            # fail early here rather than deep inside load_content()
            if not self._llm_client.get_vision_model():
                raise RuntimeError("%sDocument: LLM_MODEL_VISION not configured, cannot process documents" % self._get_type_name())

            # initialise the converter in a sub-directory so its output files
            # do not collide with other artefacts written to the working dir
            self._converter = DocumentConverter(
                helper_config=self._helper_config,
                working_directory=os.path.join(self._working_directory, "conversions"),
            )
            self.logging.debug("Document converter initialized for %s '%s'", self._get_type_name(), self._source_file, color="green")

            # convert to a format that the extraction strategies can handle (e.g. PDF)
            self._converted_file = self._converter.convert(self._source_file)
            self._converted_extension = self._helper_file.get_file_extension(self._converted_file, True, True)
            self._source_extension = self._helper_file.get_file_extension(self._source_file, True, True)
            self._source_filename = self._helper_file.get_basename(self._source_file, True)
            self.logging.debug("%sDocument now in supported format: '%s'", self._get_type_name(), self._converted_file, color="green")
        except Exception as e:
            # clean up the working directory before re-raising so the caller
            # does not need to call cleanup() after a failed boot()
            self.cleanup()
            raise e

    @abstractmethod
    def _before_cleanup(self) -> None:
        """
        Runs before the regular cleanup process
        """

    def cleanup(self) -> None:
        """
        Remove the working directory and reset all bootable state.

        Must be called in a ``finally`` block after ``boot()`` to ensure
        temporary files are deleted even when ingestion fails.
        Safe to call even if ``boot()`` was never called or failed midway.
        """
        # run the subclass-specific pre-cleanup actions
        self._before_cleanup()

        # delete the working dir — warn but do not raise if deletion fails
        if not self._helper_file.remove_folder(self._working_directory):
            self.logging.warning("%sDocument failed to delete working directory '%s' for converted files",self._get_type_name(), self._working_directory)

        # reset all bootable fields so the object cannot be accidentally reused
        # helper
        self._converter = None

        # file
        self._converted_file = None
        self._source_filename = None
        self._source_extension = None
        self._converted_extension = None

        # content
        self._content_needs_formatting = None
        self._page_contents = None
        self._final_content = None

        # metadata
        self._metadata_final = None
        self._tags = None

    ##########################################
    ############# CHECKER ####################
    ##########################################

    def is_booted(self) -> bool:
        """
        Return True if the working directory exists and the converter is ready.

        Returns:
            bool: True if the document is booted and ready for content extraction, False otherwise.
        """
        return (
            self._helper_file.folder_exists(self._working_directory)
            and self._converter is not None
            and self._converter.is_booted()
        )

    ##########################################
    ############## GETTER ####################
    ##########################################

    @abstractmethod
    def _get_type_name(self) -> str:
        """
        Return a human-readable name for the document type, used for fetching correct ENV Vars.
        """
        pass

    @abstractmethod
    def _get_additional_metadata(self) -> DocMetadata:
        """
        Return metadata derived from the subclass

        Returns:
            DocMetadata: The metadata generated by subclass
        """
        pass    

    def _get_direct_read_file_formats(self) -> list[str]:
        """
        Return extensions that can be read as plain text without page iteration.
        """
        return ["txt", "md"]

    def _get_programmatic_read_file_formats(self) -> list[str]:
        """
        Return extensions that support programmatic text extraction via PyMuPDF.
        """
        return ["pdf", "txt", "md", "docx", "doc", "odt", "ott", "xlsx", "xls",
                "ods", "csv", "pptx", "ppt", "odp", "rtf"]

    def get_source_file(self, filename_only: bool = False) -> str | None:
        """
        Return the original source file path or just the filename.

        Args:
            filename_only (bool): When True returns only the basename (with extension).

        Returns:
            str | None: Absolute path or basename string, or None if not booted.

        Raises:
            RuntimeError: If the document has not been booted yet.
        """
        if not self.is_booted():
            raise RuntimeError("%sDocument: cannot get source file, document not booted" % self._get_type_name())
        if filename_only:
            return self._source_filename
        return self._source_file

    def get_title(self) -> str:
        """
        Return the document title as '{correspondent} {document_type} {DD.MM.YYYY}'.
        """
        return (
            "%s %s %s.%s.%s"
            % (
                self._metadata_final.correspondent,
                self._metadata_final.document_type,
                self._metadata_final.day,
                self._metadata_final.month,
                self._metadata_final.year,
            )
        )

    def get_metadata(self) -> DocMetadata:
        """
        Return the fully merged metadata (path template + LLM fill-in).
        """
        return self._metadata_final

    def get_tags(self) -> list[str]:
        """
        Return the LLM-extracted tag list, or an empty list if none were found.
        """
        return self._tags or []

    def get_content(self) -> str:
        """
        Return the Markdown-formatted document content.
        """
        return self._final_content

    def get_date_string(self, pattern: str = "%Y-%m-%d") -> str | None:
        """
        Return the document creation date as a string in the given format.

        Args:
            pattern (str): ``strftime`` format string (default: ``'%Y-%m-%d'``).

        Returns:
            str | None: Formatted date string, or None if year is missing or the date is invalid.
        """
        if not self._metadata_final.year:
            return None
        month = self._metadata_final.month or "01"
        day = self._metadata_final.day or "01"
        try:
            from datetime import datetime
            dt = datetime(int(self._metadata_final.year), int(month), int(day))
            return dt.strftime(pattern)
        except ValueError:
            return None

    def get_file_bytes(self) -> bytes:
        """
        Return the original file content as bytes.
        """
        return self._file_bytes

    def get_file_hash(self) -> str:
        """
        Return the precomputed SHA-256 hex digest of the original source file.
        """
        return self._original_file_hash
    
    def get_additional_tags(self) -> list[str]:
        """
        Returns the list of additional tags currently set
        Used only for giving the llm hints about already existing values

        Returns:
            list[str]: The list of currently saved additional tags
        """
        return self._additional_tags
    
    def get_additional_correspondents(self) -> list[str]:
        """
        Returns the list of additional correspondents currently set
        Used only for giving the llm hints about already existing values

        Returns:
            list[str]: The list of currently saved additional correspondents
        """
        return self._additional_correspondents

    def get_additional_document_types(self) -> list[str]:
        """
        Returns the list of additional document types currently set
        Used only for giving the llm hints about already existing values

        Returns:
            list[str]: The list of currently saved additional document types
        """
        return self._additional_doc_types

    ##########################################
    ############## SETTER ####################
    ##########################################

    def add_additional_tags(self, tags:list[str])->None:
        """
        Adds the given tags to additional tag list if not already present
        Used only for giving the llm hints about already existing values

        Args:
            tags (list[str]): The tags to add
        """
        self._additional_tags = self._merge_list_unique(self._additional_tags, tags)

    def add_additional_correspondents(self, correspondents:list[str])->None:
        """
        Adds the given correspondents to additional correspondent list if not already present
        Used only for giving the llm hints about already existing values

        Args:
            correspondents (list[str]): The correspondents to add
        """
        self._additional_correspondents = self._merge_list_unique(self._additional_correspondents, correspondents)

    def add_additional_document_types(self, doc_types:list[str])->None:
        """
        Adds the given document types to additional document type list if not already present
        Used only for giving the llm hints about already existing values

        Args:
            doc_types (list[str]): The document types to add
        """
        self._additional_doc_types = self._merge_list_unique(self._additional_doc_types, doc_types)

    ##########################################
    ############### CORE #####################
    ##########################################

    async def load_content(self) -> None:
        """
        Extract raw text from the converted file.

        Tries each reading strategy in priority order:
          1. Direct read   — for txt/md files (no page iteration needed).
          2. OCR client    — if configured and not explicitly skipped.
          3. Vision LLM    — page-by-page image → text.
          4. Programmatic  — PyMuPDF text extraction.

        Raises:
            RuntimeError: If the document is not booted, if no reading strategy
                is applicable, or if all strategies return empty content.
        """
        if not self.is_booted():
            raise RuntimeError("Document: cannot extract text, document not booted")

        # direct read is the cheapest path — use it for plain-text formats first
        if self._converted_extension in self._get_direct_read_file_formats() and not self._skip_direct_read:
            self._load_content_directly()

        # OCR client converts the whole PDF to Markdown in one call — preferred when available
        elif self._ocr_client is not None and not self._skip_ocr_read:
            await self._load_content_ocr()

        # Vision LLM is the highest-quality fallback for scanned/image-heavy PDFs
        elif not self._skip_vision_read:
            await self._load_content_vision()

        # programmatic extraction via PyMuPDF — fastest for text-based PDFs
        elif self._source_extension in self._get_programmatic_read_file_formats() and not self._skip_programmatic_read:
            self._load_content_programmatic()

        # no usable strategy for this file with the current configuration
        else:
            raise RuntimeError(
                "Document: no valid content loading method available for file '%s' with current configuration"
                % self._source_filename
            )

        # all strategies must produce at least one non-empty page
        if not self._page_contents:
            raise RuntimeError("No content extracted from document '%s'" % self._converted_file)

    async def format_content(self) -> None:
        """
        Merge raw pages into final Markdown via Chat LLM (when required).

        For directly-read or OCR content that already arrives fully formatted,
        pages are simply joined with double newlines and no LLM call is made.
        For programmatically-extracted or vision-extracted content the Chat LLM
        is used to clean up each page and then merge cross-page boundaries.

        Raises:
            RuntimeError: If page contents are missing, or if an LLM call
                fails or returns empty/too-short content.
        """
        # guard: content must be loaded before formatting
        if not self._page_contents:
            raise RuntimeError("Cannot format content, no page contents available")

        # when the reading strategy already produces clean Markdown (e.g. OCR,
        # direct read) skip the LLM formatting pass entirely
        if not self._content_needs_formatting:
            self._final_content = "\n\n".join(self._page_contents)
            return

        # run Chat LLM over each raw page to produce clean Markdown
        formatted_pages: list[str] = []
        for idx, page in enumerate(self._page_contents):
            formatted = await self._call_chat_llm_format(page)

            # reject pages that are effectively empty after formatting — the
            # pipeline cannot produce meaningful metadata from near-blank pages
            if len(formatted) >= self._minimum_text_chars_for_direct_read:
                formatted_pages.append(formatted)
                self.logging.info(
                    "Formatted text page %d/%d by Chat LLM from file '%s'",
                    idx + 1, len(self._page_contents), self._source_filename,
                    color="green"
                )
                self.logging.debug(formatted, color="blue")
            else:
                raise RuntimeError(
                    "Formatted content from Chat LLM is too short or empty for page %d/%d of file '%s'"
                    % (idx + 1, len(self._page_contents), self._source_filename)
                )

        self.logging.info("Formatted pages from '%s'", self._source_filename, color="green")
        self.logging.debug(formatted_pages, color="blue")

        # clean repeated headers/footers before merging so they do not pollute
        # the boundary-detection logic or the final document
        formatted_pages = self._remove_repeated_headers_footers(formatted_pages)
        formatted_pages = self._stitch_table_continuations(formatted_pages)
        self._final_content = await self._call_chat_llm_merge(formatted_pages)
        if not self._final_content.strip():
            self._final_content = None
            raise RuntimeError(
                "Final merged content from Chat LLM is empty for file '%s'" % self._source_filename
            )
        self.logging.info("Merged formatted pages from '%s'", self._source_filename, color="green")
        self.logging.debug(self._final_content, color="blue")

    async def load_metadata(self) -> None:
        """
        Merge subclass-derived metadata with LLM-extracted metadata.

        Subclass metadata are always preferred when present, and LLM metadata are only used to fill in missing fields. Except for tags, which are always merged.

        Date fields missing from both sources fall back to the file modification date. 
        Missing title falls back to the filename stem.  
        Missing document_type falls back to a generic placeholder.  
        When any fallback is applied a 'Todo' tag is added to signal manual review.

        Raises:
            RuntimeError: If final content is missing, if any mandatory metadata field is still empty after extraction and fallbacks, or if the LLM call fails.
        """
        # final content is the sole input to the LLM extraction prompt
        if not self._final_content:
            raise RuntimeError("%sDocument cannot extract metadata -> no final content available" % self._source_filename)

        # ask the LLM to fill in whatever the path template could not provide
        llm_meta = await self._call_chat_llm_meta()

        # retrieve any subclass-derived metadata — may be None for mail documents
        subclass_meta = self._get_additional_metadata()

        # merge subclass tags into the LLM tag list without duplicates (case-insensitive)
        llm_meta_tags = self._merge_list_unique(subclass_meta.tags, llm_meta.tags)

        # subclass fields win — only fall back to LLM when the subclass provided no value
        content_meta = DocMetadata(
            correspondent=subclass_meta.correspondent or llm_meta.correspondent,
            document_type=subclass_meta.document_type or llm_meta.document_type,
            year=subclass_meta.year or llm_meta.year,
            month=subclass_meta.month or llm_meta.month,
            day=subclass_meta.day or llm_meta.day,
            title=subclass_meta.title or llm_meta.title,
            tags=llm_meta_tags,
            filename=self._helper_file.get_basename(self._source_file, True))

        # ask the subclass for context-specific fallbacks (e.g. email header date) that are
        # more meaningful than generic file-system defaults; applied before file-mtime below
        meta_fallback = self._get_fallback_metadata()

        # apply date fallbacks — prefer subclass fallback over file modification time so
        # mail documents use the email Date header rather than a meaningless temp-file mtime
        fallback_applied = False
        file_mtime = os.path.getmtime(self._source_file)
        file_date = datetime.datetime.fromtimestamp(file_mtime)
        if not content_meta.year:
            if meta_fallback.year:                
                content_meta.year = meta_fallback.year
            else:
                content_meta.year = str(file_date.year)           
                fallback_applied = True
            self.logging.info("Year missing — using fallback year '%s' for '%s'", content_meta.year, self._source_filename, color="magenta")
        if not content_meta.month:
            if meta_fallback.month:
                content_meta.month = meta_fallback.month
            else:
                content_meta.month = "%02d" % file_date.month
            self.logging.info("Month missing — using fallback month '%s' for '%s'",content_meta.month, self._source_filename, color="magenta")
            fallback_applied = True
        if not content_meta.day:
            if meta_fallback.day:
                content_meta.day = meta_fallback.day
            else:
                content_meta.day = "%02d" % file_date.day
            self.logging.info("Day missing — using fallback day '%s' for '%s'",content_meta.day, self._source_filename, color="magenta")
            fallback_applied = True

        # fall back to the filename stem as title for untitled documents; subclass fallback
        # (e.g. "subject — filename" for mail attachments) takes precedence over filename stem
        if not content_meta.title:
            if meta_fallback.title:
                content_meta.title = meta_fallback.title
            else:
                content_meta.title = self._helper_file.get_basename(self._source_file, False)
            self.logging.info("Title missing — using fallback title '%s' for '%s'", content_meta.title, self._source_filename, color="magenta")
            fallback_applied = True

        # apply correspondent fallback when the LLM extracted nothing; does not flag
        # fallback_applied because a missing correspondent is not a quality gap that
        # requires manual review — it is an expected substitution (e.g. email sender)
        if not content_meta.correspondent and meta_fallback.correspondent:
            content_meta.correspondent = meta_fallback.correspondent
            self.logging.info("Correspondent missing — using fallback '%s' for '%s'",meta_fallback.correspondent, self._source_filename, color="magenta")

        # use a generic placeholder when neither subclass nor LLM produced a document_type
        if not content_meta.document_type:
            content_meta.document_type = "Unknown Document Type"
            self.logging.info(
                "Document type missing — using fallback 'Unknown Document Type' for '%s'",
                self._source_filename, color="magenta"
            )
            fallback_applied = True

        # add a "Todo" tag whenever any fallback was applied — signals manual review
        if fallback_applied and "todo" not in [t.lower() for t in content_meta.tags]:
            content_meta.tags.append("Todo")

        # validate all scalar fields are present — an empty field causes a broken DMS entry
        skip_fields = {"tags", "quarter"}
        for field_name in content_meta.__dataclass_fields__.keys():
            if field_name in skip_fields:
                continue
            if not getattr(content_meta, field_name):
                raise RuntimeError("'%s' is missing after LLM extraction" % field_name)

        # save the meta to this instance
        self._metadata_final = content_meta
        # add llm_meta_tags to additional tags, already takes care for duplicates. We don't use content.meta_tags here to avoid adding "Todo"
        self.add_additional_tags(llm_meta_tags)
        # log as dict for better readability in logs
        self.logging.info("Metadata loaded for '%s'", self._source_filename, color="green")
        self.logging.debug(self._metadata_final.__dataclass_fields__, color="blue")

    async def load_tags(self) -> None:
        """
        Extract tags from the final content via Chat LLM.

        Raises:
            RuntimeError: If final content is missing or if the LLM call fails.
        """
        # final content must be available as input to the LLM prompt
        if not self._final_content:
            raise RuntimeError("%sDocument: cannot extract tags -> no final content available" % self._source_filename)

        # extract tags using llm
        llm_tags = []
        try:
            llm_tags = await self._call_chat_llm_tags()
            # sanitize
            llm_tags = [t.strip() for t in llm_tags if t.strip()]
        except Exception as e:
            self.logging.warning("LLM tag extraction failed for '%s': %s. Checking if we have some meta from subclass...", self._source_filename, str(e))

        # getting tags from subclass
        subclass_meta = self._get_additional_metadata()
        # if we have neither LLM tags nor subclass tags, we throw an error
        if not llm_tags and not subclass_meta.tags:
            raise RuntimeError("No tags extracted from LLM or subclass for '%s'" % self._source_filename)
        
        # merge the llm tags with subclass tags while avoid duplicates and prioritizing subclass tags
        llm_tags = self._merge_list_unique(subclass_meta.tags, llm_tags)
        # add to additional tags and save to this instance        
        self._tags = llm_tags
        self.add_additional_tags(llm_tags)

        self.logging.info("Tags loaded for '%s'", self._source_filename, color="green")
        self.logging.debug(self._tags, color="blue")

    ##########################################
    ############ CONTENT LOADER ##############
    ##########################################

    def _load_content_directly(self) -> None:
        """
        Read the file as plain text (for txt/md formats). No formatting pass needed.
        """
        # guard: warn when called for unsupported formats or with the flag disabled
        unsupported = self._converted_extension not in self._get_direct_read_file_formats()
        if unsupported:
            self.logging.warning("Called _load_content_directly for an unsupported file!")
        if self._skip_direct_read:
            self.logging.warning("Called _load_content_directly with _skip_direct_read configuration!")
        if unsupported or self._skip_direct_read:
            # mark content as empty so the caller can fall through to the next strategy
            self._page_contents = []
            self._content_needs_formatting = True
            return

        # read the file as UTF-8 text — HelperFile handles encoding errors gracefully
        text = self._helper_file.read_text_file(self._converted_file)
        text = "" if text is None else text.strip()
        if not text:
            self.logging.error(
                "Error reading text directly from file '%s'. Empty content",
                self._converted_file
            )
            self._page_contents = []
            self._content_needs_formatting = False
        else:
            self._page_contents = [text]
            # direct-read content is already in its final form — no LLM formatting needed
            self._content_needs_formatting = False
            self.logging.info("Read text directly from '%s'", self._source_filename, color="green")
            self.logging.debug(self._page_contents, color="blue")

    async def _load_content_ocr(self) -> None:
        """
        Read the file using the OCR client (e.g. Docling).

        Falls back to Vision LLM if the OCR call is misconfigured or returns
        empty content.
        """
        # guard: these indicate programming errors — the caller should not reach here
        if self._skip_ocr_read:
            self.logging.warning(
                "Called _load_content_ocr with skip_ocr_read configuration! Falling back to vision reading..."
            )
        if self._ocr_client is None:
            self.logging.warning(
                "Called _load_content_ocr without an OCR client configured! Falling back to vision reading..."
            )
        if self._skip_ocr_read or self._ocr_client is None:
            await self._load_content_vision()
            return

        self._page_contents = await self._read_file_ocr()
        # OCR output is already structured Markdown — no additional formatting pass needed
        self._content_needs_formatting = False
        if not self._page_contents:
            # OCR returned nothing usable — degrade to the Vision LLM path
            self.logging.warning("OCR failed, falling back to vision reading...")
            await self._load_content_vision()

    async def _load_content_vision(self) -> None:
        """
        Read the file page-by-page via Vision LLM.

        Falls back to programmatic reading if the vision pass is skipped or
        produces empty output.
        """
        if self._skip_vision_read:
            self.logging.warning(
                "Called _load_content_vision with _skip_vision_read configuration! Falling back to programmatic reading..."
            )
            self._load_content_programmatic()
            return

        self._page_contents = await self._read_file_vision()
        # vision output is clean structured Markdown — no additional formatting pass needed
        self._content_needs_formatting = False
        if not self._page_contents:
            self.logging.warning("Vision failed, falling back to programmatic reading...")
            self._load_content_programmatic()

    def _load_content_programmatic(self) -> None:
        """
        Read the file using PyMuPDF text extraction. Falls back to direct reading if unsupported or disabled.
        """
        unsupported = self._source_extension not in self._get_programmatic_read_file_formats()
        if unsupported:
            self.logging.warning("Called _load_content_programmatic for an unsupported file!")
        if self._skip_programmatic_read:
            self.logging.warning("Called _load_content_programmatic with _skip_programmatic_read configuration!")
        if unsupported or self._skip_programmatic_read:
            self._load_content_directly()
            return

        self._page_contents = self._read_file_programatically()
        # programmatic output retains raw PDF text layout — it must go through
        # the Chat LLM formatting step before it is usable as clean Markdown
        self._content_needs_formatting = True
        if not self._page_contents:
            self.logging.warning("Programmatic read failed, falling back to direct reading...")
            self._load_content_directly()

    ##########################################
    ############ CONTENT READER ##############
    ##########################################

    def _read_file_programatically(self) -> list[str]:
        """
        Use PyMuPDF to extract text from each page of the converted file.

        Skips pages whose extracted text falls below the minimum character
        threshold — these are assumed to be image-only pages that the
        programmatic path cannot handle.

        Returns:
            list[str]: List of extracted page text strings (one entry per non-empty page).
        """
        page_texts: list[str] = []
        try:
            doc = fitz.open(self._converted_file)
            for page_num, page in enumerate(doc):
                direct_text = page.get_text().strip()

                # skip pages that contain too little text — they are likely
                # scanned images and would pollute the content with empty strings
                if len(direct_text) >= self._minimum_text_chars_for_direct_read:
                    page_texts.append(direct_text)
                    self.logging.info(
                        "Extracted text page %d/%d programmatically from file '%s'",
                        page_num + 1, len(doc), self._source_filename, color="green"
                    )
                    self.logging.debug(direct_text, color="blue")
                else:
                    self.logging.info(
                        "No text extracted programmatically from page %d/%d of file '%s'",
                        page_num + 1, len(doc), self._source_filename, color="yellow"
                    )
            doc.close()
        except Exception as e:
            self.logging.error(
                "Error extracting text programmatically from file '%s': %s",
                self._converted_file, e
            )
            return []
        return page_texts

    async def _read_file_vision(self) -> list[str]:
        """
        Use the Vision LLM to extract text from each page as a rasterised image.

        Each page is rendered to a PNG at ``_page_dpi`` and sent to the Vision
        LLM.  A short trailing context from the previous page is prepended to
        each prompt so the model can recognise cross-page continuations.
        Pages whose extracted text is too short fall back to direct PyMuPDF
        extraction before being skipped.

        Returns:
            list[str]: List of extracted page text strings (one entry per non-empty page).
        """
        page_texts: list[str] = []
        # how many trailing characters of the previous page to include for context
        vision_context_chars = self._helper_config.get_number_val("FILE_INGESTION_VISION_CONTEXT_CHARS", 300)
        try:
            doc = fitz.open(self._converted_file)
            for page_num, page in enumerate(doc):
                # render the page to a PNG and encode it as base64 for the Vision LLM
                pix = page.get_pixmap(dpi=self._page_dpi)
                png_bytes = pix.tobytes("png")
                b64 = base64.b64encode(png_bytes).decode("ascii")

                # pass the tail of the previous page as context so the model
                # can identify mid-page continuations of sentences or tables
                context_before = page_texts[-1][-vision_context_chars:] if page_texts else ""

                text = await self._call_vision_llm(b64, context_before)

                # accept the page if it has enough content; otherwise fall back
                # to PyMuPDF direct extraction before giving up
                if len(text) >= self._minimum_text_chars_for_direct_read:
                    page_texts.append(text)
                    self.logging.info(
                        "Extracted text page %d/%d by vision LLM from file '%s'",
                        page_num + 1, len(doc), self._source_filename, color="green"
                    )
                    self.logging.debug(text, color="blue")
                else:
                    self.logging.info(
                        "No text extracted by vision LLM from page %d/%d of file '%s' --> Falling back to direct reading",
                        page_num + 1, len(doc), self._source_filename, color="yellow"
                    )
                    # fallback: try PyMuPDF direct text extraction for this page only
                    direct_text = page.get_text().strip()
                    if len(direct_text) >= self._minimum_text_chars_for_direct_read:
                        page_texts.append(direct_text)
                        self.logging.info(
                            "Extracted text page %d/%d by direct reading from file '%s'",
                            page_num + 1, len(doc), self._source_filename, color="green"
                        )
                        self.logging.debug(direct_text, color="blue")
                    else:
                        self.logging.info(
                            "No text extracted by fallback direct reading from page %d/%d of file '%s'",
                            page_num + 1, len(doc), self._source_filename, color="yellow"
                        )
            doc.close()
        except Exception as e:
            self.logging.error(
                "Error extracting text by vision LLM from file '%s': %s",
                self._source_filename, e
            )
            return []
        return page_texts

    async def _read_file_ocr(self) -> list[str]:
        """
        Use the OCR client (e.g. Docling) to convert the file to Markdown.

        Returns the entire document as a single-element list containing the
        full Markdown string produced by the OCR service.

        Returns:
            list[str]: Single-element list with the complete Markdown string, or an empty list if the OCR call fails or returns empty content.
        """
        try:
            with open(self._converted_file, "rb") as f:
                file_bytes = f.read()
            filename = self._helper_file.get_basename(self._converted_file, with_extension=True)
            markdown = await self._ocr_client.do_convert_pdf_to_markdown(file_bytes, filename)
            if not markdown or not markdown.strip():
                self.logging.error(
                    "Error extracting text by OCR client %s from file '%s'. Empty content",
                    self._ocr_client.get_engine_name(), self._source_filename
                )
                return []
            self.logging.info(
                "Extracted text by OCR client %s from file '%s'",
                self._ocr_client.get_engine_name(), self._source_filename, color="green"
            )
            self.logging.debug(markdown, color="blue")
            return [markdown]
        except Exception as e:
            self.logging.error(
                "Error extracting text by OCR client %s from file '%s': %s",
                self._ocr_client.get_engine_name(), self._source_filename, e
            )
            return []

    ##########################################
    ################ CACHE ###################
    ##########################################

    def _get_dms_cache(self) -> dict[str, list[str]]:
        """
        Read document type, tag, and correspondent names from the DMS cache.

        Used to provide the LLM with existing DMS values as hints so it
        prefers reusing established names over inventing new ones.

        Returns:
            dict[str, list[str]]: Dict with keys 'document_types', 'tags', and/or 'correspondents' mapping to sorted name lists. Returns an empty dict when no DMS client is available.
        """
        if not self._dms_client:
            return {}
        result: dict[str, list[str]] = {}
        if self._dms_client._cache_document_types:
            names = sorted({
                dt.name for dt in self._dms_client._cache_document_types.values()
                if dt.name
            })
            if names:
                result["document_types"] = names
        if self._dms_client._cache_tags:
            names = sorted({
                t.name for t in self._dms_client._cache_tags.values()
                if t.name
            })
            if names:
                result["tags"] = names
        if self._dms_client._cache_correspondents:
            names = sorted({
                c.name for c in self._dms_client._cache_correspondents.values()
                if c.name
            })
            if names:
                result["correspondents"] = names
        return result

    def _get_from_cache(self, key: str, additionals: list[str] | None) -> list[str]:
        """
        Return DMS-cached values for the given key.
        Adds the provided additionals to the returned values, ensuring no duplicates (case-insensitive) and preserving original capitalisation.

        Args:
            key (str): The cache key to retrieve values for (e.g. "document_types", "tags", "correspondents").
            additionals (list[str] | None): Optional extra strings to merge into the result.

        Returns:
            list[str]: Merged, deduplicated list of strings.
        """
        additionals = [] if additionals is None else additionals
        cache = self._get_dms_cache()
        cache_elements = cache.get(key, []) if cache else []
        cache_elements_lower = [element.lower() for element in cache_elements]
        merged_elements = list(cache_elements) # flat copy to avoid mutating the original cache list
        if additionals:
            # append each additional element only if it is not already present —
            # compare case-insensitively but keep the original capitalisation
            for element in additionals:
                if element.lower() not in cache_elements_lower:
                    merged_elements.append(element)
        return merged_elements

    ##########################################
    ################# LLM ####################
    ##########################################

    async def _call_vision_llm(self, png_b64_data: str, context_before: str) -> str:
        """
        Send a page image to the Vision LLM and return its extracted text.

        Args:
            png_b64_data (str): Base64-encoded PNG of the page.
            context_before (str): Trailing text of the previously formatted page (may be empty for the first page).

        Returns:
            str: Text content extracted by the Vision LLM, or an empty string if the call fails.
        """
        try:
            prompt = await self._get_prompt_vision_ocr(image_bytes=png_b64_data, context=context_before)
            result = await self._llm_client.do_chat_vision(prompt["messages"])
            # strip any code fences the model may wrap its output in
            result = re.sub(r"```[a-zA-Z]*\s*\n?(.*?)\n?```", r"\1", result.strip(), flags=re.DOTALL)
            return result.strip()
        except Exception as e:
            self.logging.error(
                "Call Vision LLM %s failed for '%s': %s",
                self._llm_client.get_vision_model(), self._source_filename, e
            )
            return ""

    async def _call_chat_llm_format(self, raw: str) -> str:
        """
        Send raw extracted text to the Chat LLM for Markdown formatting.

        Args:
            raw (str): Plain text as returned by the programmatic reading step.

        Returns:
            str: Formatted Markdown string, or an empty string if the call fails.
        """
        try:
            prompt = await self._get_prompt_format(unformatted_text=raw)
            result = await self._llm_client.do_chat(prompt["messages"])
            # strip any code fences the model may wrap its output in
            result = re.sub(r"```[a-zA-Z]*\s*\n?(.*?)\n?```", r"\1", result.strip(), flags=re.DOTALL)
            return result.strip()
        except Exception as e:
            self.logging.error(
                "Call Chat LLM %s for formatting failed for '%s': %s",
                self._llm_client.get_chat_model(), self._source_filename, e
            )
            return ""

    async def _call_chat_llm_merge(self, formatted_pages: list[str]) -> str:
        """
        Detect cross-page content flow and join those boundaries via Chat LLM. Falls back to plain newline join on failure.

        Args:
            formatted_pages (list[str]): Per-page Markdown strings (after cleanup).

        Returns:
            str: Final assembled Markdown document string.
        """
        # single-page documents need no merging — return immediately
        if len(formatted_pages) == 1:
            return formatted_pages[0]

        merge_boundaries: set[int] = set()
        try:
            prompt = await self._get_prompt_merge(pages=formatted_pages)
            raw = await self._llm_client.do_chat(prompt["messages"])
            data = self._prompt_client.validate_prompt_schema(prompt["config"], raw)
            if data is None:
                raise ValueError("LLM response did not match expected schema!")
            merge_boundaries = set(int(b) for b in (data.get("merge_at_boundaries") or []))
        except Exception as e:
            self.logging.warning(
                "Call Chat LLM %s for merging failed for '%s': %s.\nContinue joining pages as-is.",
                self._llm_client.get_chat_model(), self._source_filename, e, color="yellow"
            )

        # assemble the final document, stitching only the identified boundaries
        result_parts: list[str] = []
        i = 0
        while i < len(formatted_pages):
            current = formatted_pages[i]
            while i + 1 < len(formatted_pages) and i in merge_boundaries:
                i += 1
                current = current.rstrip() + "\n" + formatted_pages[i].lstrip()
            result_parts.append(current)
            i += 1
        return "\n\n".join(result_parts).strip()

    async def _call_chat_llm_meta(self) -> DocMetadata:
        """
        Extract metadata from the formatted document content via Chat LLM.

        Returns:
            DocMetadata: The populated metadata from the LLM JSON response.

        Raises:
            RuntimeError: If the LLM call fails or the response cannot be parsed according to the expected schema.
        """
        try:
            prompt = await self._get_prompt_extraction()
            raw = await self._llm_client.do_chat(prompt["messages"])
            data = self._prompt_client.validate_prompt_schema(prompt["config"], raw)
            if data is None:
                raise ValueError("LLM response did not match expected schema!")

            return DocMetadata(
                correspondent=data.get("correspondent") or None,
                document_type=data.get("document_type") or None,
                year=data.get("year") or None,
                month=data.get("month") or None,
                day=data.get("day") or None,
                title=data.get("title") or None,
                filename=self._helper_file.get_basename(self._source_file, True),
            )
        except Exception as e:
            raise RuntimeError(
                "Failed to read meta from content using llm '%s': %s" % (self._source_file, e)
            )

    async def _call_chat_llm_tags(self) -> list[str]:
        """
        Extract tags from the formatted document content via Chat LLM.

        Returns:
            list[str]: Non-empty list of tag name strings.

        Raises:
            RuntimeError: If the LLM call fails, the response cannot be parsed, or the tag list is empty.
        """
        try:
            prompt = await self._get_prompt_tags()
            raw = await self._llm_client.do_chat(prompt["messages"])
            data = self._prompt_client.validate_prompt_schema(prompt["config"], raw)
            if data is None:
                raise ValueError("LLM response did not match expected schema!")

            # an empty tag list is a soft error — the document must have at least one tag
            if not data["tags"]:
                raise ValueError("Tag extraction LLM response is an empty list for file '%s'" % self._source_file)
            return data["tags"]
        except Exception as e:
            raise RuntimeError("Failed to read tags from content using llm '%s': %s" % (self._source_file, e))

    ##########################################
    ############### PROMPTS ##################
    ##########################################

    async def _get_prompt_extraction(self) -> dict[str, Union[PromptConfig, list[dict[str, str]]]]:
        """
        Fetch and render the metadata extraction prompt with DMS cache hints.

        Returns:
            dict[str, Union[PromptConfig, list[dict[str, str]]]]: Dict with keys 'config' (PromptConfig) and 'messages' (list of role/content dicts) ready to be sent to the Chat LLM.
        """
        doc_types = self._get_from_cache("document_types", self.get_additional_document_types())
        correspondents = self._get_from_cache("correspondents", self.get_additional_correspondents())
        tags = self._get_from_cache("tags", self.get_additional_tags())

        replacements = {
            "company": self._owner_company_name,
            "language": self._language,
            "document_content": self._final_content,
            "existing_document_types": ", ".join(doc_types) if doc_types else "(none)",
            "existing_correspondents": ", ".join(correspondents) if correspondents else "(none)",
            "existing_tags": ", ".join(tags) if tags else "(none)",
        }
        prompt:PromptConfig = await self._prompt_client.do_fetch_prompt(id="doc_ingestion_extract_metadata")
        messages:list[PromptConfigMessage] = self._prompt_client.render_prompt(
            prompt=prompt,
            replacements=replacements,
            max_chars=self._llm_client.get_chat_model_max_chars(),
        )
        dict_messages = [{"role": m.role, "content": m.content} for m in messages]
        return {"config": prompt, "messages": dict_messages}

    async def _get_prompt_tags(self) -> dict[str, Union[PromptConfig, list[dict[str, str]]]]:
        """
        Fetch and render the tag extraction prompt with DMS cache hints.

        Returns:
            dict[str, Union[PromptConfig, list[dict[str, str]]]]: Dict with keys 'config' (PromptConfig) and 'messages' (list of role/content dicts) ready to be sent to the Chat LLM.
        """
        tags = self._get_from_cache("tags", self.get_additional_tags())

        replacements = {
            "language": self._language,
            "document_content": self._final_content,
            "existing_tags": ", ".join(tags) if tags else "(none)",
        }
        prompt = await self._prompt_client.do_fetch_prompt(id="doc_ingestion_extract_tags")
        messages = self._prompt_client.render_prompt(
            prompt=prompt,
            replacements=replacements,
            max_chars=self._llm_client.get_chat_model_max_chars(),
        )
        dict_messages = [{"role": m.role, "content": m.content} for m in messages]
        return {"config": prompt, "messages": dict_messages}

    async def _get_prompt_vision_ocr(self, image_bytes: str, context: str = "") -> dict[str, Union[PromptConfig, list[dict[str, str]]]]:
        """
        Fetch and render the Vision OCR prompt, attaching the page image.

        The base64-encoded image is attached to the last user message in the
        rendered prompt template so the Vision LLM receives both the textual
        instruction and the image in the same turn.

        Args:
            image_bytes (str): Base64-encoded string of the page PNG image.
            context (str): Optional trailing text from the previous page to provide continuity hints.

        Returns:
            dict[str, Union[PromptConfig, list[dict[str, str]]]]: Dict with keys 'config' (PromptConfig) and 'messages' (list of role/content dicts) ready to be sent to the Chat LLM.

        Raises:
            ValueError: If the rendered prompt contains no user message to
                attach the image to.
        """
        replacements = {
            "previous_context": context if context else "N/A",
        }
        prompt = await self._prompt_client.do_fetch_prompt(id="doc_ingestion_vision_ocr")
        messages = self._prompt_client.render_prompt(prompt=prompt, replacements=replacements)
        dict_messages = [{"role": m.role, "content": m.content} for m in messages]

        # attach the image to the last user message — the Vision LLM API requires
        # the image to be co-located with the instruction that references it
        image_appended = False
        for m in reversed(dict_messages):
            if m["role"] == "user":
                m["images"] = [image_bytes]
                image_appended = True
                break

        if not image_appended:
            raise ValueError(
                "Vision OCR prompt template must contain at least one user message to attach "
                "the image to. Please check the prompt configuration for 'doc_ingestion_vision_ocr'."
            )

        return {"config": prompt, "messages": dict_messages}

    async def _get_prompt_format(self, unformatted_text: str) -> dict[str, any]:
        """
        Fetch and render the document formatting prompt.

        Validates that the rendered prompt does not exceed the model's max
        character limit — oversized prompts would be silently truncated by the
        LLM and produce garbled output.

        Args:
            unformatted_text (str): Raw text of the page to be formatted.

        Returns:
            dict[str, any]: Dict with keys 'config' (PromptConfig) and 'messages' (list of role/content dicts) ready to be sent to the Chat LLM.

        Raises:
            ValueError: If the rendered prompt exceeds the model's max chars.
        """
        replacements = {"raw_content": unformatted_text}
        prompt = await self._prompt_client.do_fetch_prompt(id="doc_ingestion_document_formatter")
        messages = self._prompt_client.render_prompt(prompt=prompt, replacements=replacements)

        # reject prompts that exceed the model limit before sending — an oversized
        # prompt would be silently truncated, producing garbled or incomplete output
        total_length = sum(len(m.content) for m in messages)
        if total_length > self._llm_client.get_chat_model_max_chars():
            raise ValueError(
                "Formatted prompt messages exceed the maximum allowed characters for the chat "
                "model (%d > %d). Consider reducing the input text or increasing the model max "
                "chars if possible." % (total_length, self._llm_client.get_chat_model_max_chars())
            )

        dict_messages = [{"role": m.role, "content": m.content} for m in messages]
        return {"config": prompt, "messages": dict_messages}

    async def _get_prompt_merge(self, pages: list[str]) -> dict[str, any]:
        """
        Fetch and render the page-merge prompt with boundary snippets.

        Builds a boundary overview from the last 5 and first 5 non-empty lines
        of each adjacent page pair so the LLM can identify which boundaries
        represent mid-flow content that should be joined without a blank line.

        Args:
            pages (list[str]): List of per-page Markdown strings to be merged.

        Returns:
            dict[str, any]: Dict with keys 'config' (PromptConfig) and 'messages' (list of role/content dicts) ready to be sent to the Chat LLM.
        """
        boundary_snippets: list[str] = []
        for i in range(len(pages) - 1):
            lines_a = [l for l in pages[i].splitlines() if l.strip()]
            lines_b = [l for l in pages[i + 1].splitlines() if l.strip()]
            # take last 5 non-empty lines of page A and first 5 non-empty lines of page B
            # for context — this is enough to detect most sentence/list continuations
            tail_a = "\n".join(lines_a[-5:])
            head_b = "\n".join(lines_b[:5])
            boundary_snippets.append(
                "--- Boundary %d\u2192%d ---\nEnd of page %d:\n%s\nStart of page %d:\n%s"
                % (i, i + 1, i, tail_a, i + 1, head_b)
            )

        replacements = {"boundary_snippets": "\n\n".join(boundary_snippets)}
        prompt = await self._prompt_client.do_fetch_prompt(id="doc_ingestion_merge")
        messages = self._prompt_client.render_prompt(
            prompt=prompt,
            replacements=replacements,
            max_chars=self._llm_client.get_chat_model_max_chars(),
        )
        dict_messages = [{"role": m.role, "content": m.content} for m in messages]
        return {"config": prompt, "messages": dict_messages}


    ##########################################
    ################ HELPERS #################
    ##########################################

    def _get_fallback_metadata(self) -> DocMetadata:
        """
        Return fallback metadata values used when neither subclass nor LLM produced a value.

        Called from load_metadata() before file-mtime and filename-stem fallbacks are applied.
        Subclasses override to provide context-specific fallbacks that are more meaningful than
        generic file-system defaults (e.g. email header date and sender for mail documents).
        The base implementation returns an empty skeleton — all fields default to None.

        Returns:
            DocMetadata: Skeleton with only filename set; all other fields None.
        """
        return DocMetadata(filename=self._helper_file.get_basename(self._source_file, True))

    def _merge_list_unique(self, primary_list:list[str], secondary_list:list[str])-> list[str]:
        """
        Adds the elements from secondary list to primary list, if the element does not already exist (case-insensitive)

        Args:
            primary_list (list[str]): The list to which unique elements will be added to
            secondary_list (list[str]): The list from which elements will be taken and added to primary list if unique

        Returns:
            list[str]: The merged list with sanitized unique entry
        """
        primary_lc = [el.strip().lower() for el in primary_list if el.strip()]
        primary_sanitized = [el.strip() for el in primary_list if el.strip()]
        secondary_sanitized = [el.strip() for el in secondary_list if el.strip()]

        for el in secondary_sanitized:
            if el.lower() not in primary_lc:
                primary_sanitized.append(el)
        return primary_sanitized


    def _remove_repeated_headers_footers(self, formatted_pages: list[str]) -> list[str]:
        """
        Strip lines that appear verbatim at the top/bottom of most pages.

        Detects headers and footers by counting how often each line appears
        within the first or last three lines of all pages.  Lines that occur
        in at least 60 % of pages (minimum 2) are treated as repeated and
        removed so they do not pollute the merged final document.

        Args:
            formatted_pages (list[str]): Formatted text of each page as a list of strings.

        Returns:
            list[str]: Cleaned pages with repeated boundary lines stripped.
        """
        # single-page documents have nothing to compare against
        if len(formatted_pages) < 2:
            return formatted_pages

        # count how often each boundary line appears across all pages
        header_counts: Counter = Counter()
        footer_counts: Counter = Counter()
        for page in formatted_pages:
            lines = [l for l in page.splitlines() if l.strip()]
            for line in lines[:3]:
                header_counts[line.strip()] += 1
            for line in lines[-3:]:
                footer_counts[line.strip()] += 1

        # 60 % threshold catches consistent headers/footers while tolerating
        # occasional variations (e.g. page-number-only lines that differ per page)
        threshold = max(2, len(formatted_pages) * 0.6)
        repeated_headers = {line for line, count in header_counts.items() if count >= threshold}
        repeated_footers = {line for line, count in footer_counts.items() if count >= threshold}

        if not repeated_headers and not repeated_footers:
            return formatted_pages

        self.logging.debug(
            "Removing repeated headers %s and footers %s for '%s'",
            repeated_headers, repeated_footers, self._source_filename
        )

        # strip the identified repeated lines from the start and end of each page
        cleaned_pages: list[str] = []
        for page in formatted_pages:
            lines = page.splitlines()
            while lines and lines[0].strip() in repeated_headers:
                lines.pop(0)
            while lines and lines[-1].strip() in repeated_footers:
                lines.pop()
            cleaned_pages.append("\n".join(lines).strip())
        return cleaned_pages

    def _stitch_table_continuations(self, formatted_pages: list[str]) -> list[str]:
        """
        Merge adjacent pages that share a cross-page Markdown table.

        When a page ends with a table row and the next page begins with a
        table row the two pages are joined without inserting a blank line so
        the table renders as a single contiguous block in the merged document.

        Args:
            formatted_pages (list[str]): Per-page Markdown strings (after header/footer cleanup).

        Returns:
            list[str]: Pages list with cross-page tables merged into single entries.
        """
        new_pages: list[str] = []
        i = 0
        while i < len(formatted_pages):
            new_page = formatted_pages[i]
            # keep merging as long as the boundary indicates a continuing table
            while (
                i + 1 < len(formatted_pages)
                and self._page_ends_with_table(new_page)
                and self._page_starts_with_table(formatted_pages[i + 1])
            ):
                i += 1
                # join without blank line so the Markdown table stays intact
                new_page = new_page.rstrip() + "\n" + formatted_pages[i].lstrip()
                self.logging.debug(
                    "Stitched cross-page table at boundary %d for '%s'", i, self._source_filename
                )
            new_pages.append(new_page)
            i += 1
        return new_pages

    def _page_ends_with_table(self, page: str) -> bool:
        """
        Return True if the last non-empty line of the page looks like a Markdown table row.

        A line is considered a table row when it starts with ``|`` and contains
        at least two pipe characters — this matches both data rows and separator
        rows (``| --- | --- |``).

        Args:
            page (str): Markdown content of the page.

        Returns:
            bool: True if the page likely ends with a table row.
        """
        last = next((l for l in reversed(page.splitlines()) if l.strip()), "")
        stripped = last.strip()
        return stripped.startswith("|") and stripped.count("|") >= 2

    def _page_starts_with_table(self, page: str) -> bool:
        """
        Return True if the first non-empty line of the page looks like a Markdown table row.

        Args:
            page (str): Markdown content of the page.

        Returns:
            bool: True if the page likely starts with a table row.
        """
        first = next((l for l in page.splitlines() if l.strip()), "")
        first = first.strip()
        return first.startswith("|") and first.count("|") >= 2