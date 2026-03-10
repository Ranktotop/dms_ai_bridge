from shared.helper.HelperConfig import HelperConfig
from services.doc_ingestion.helper.DocumentConverter import DocumentConverter
from services.doc_ingestion.helper.PathTemplateParser import PathTemplateParser
from services.doc_ingestion.Dataclasses import DocMetadata
from shared.clients.llm.LLMClientInterface import LLMClientInterface
from shared.clients.dms.DMSClientInterface import DMSClientInterface
from shared.clients.ocr.OCRClientInterface import OCRClientInterface
from shared.clients.prompt.PromptClientInterface import PromptClientInterface
from shared.clients.prompt.models.Prompt import PromptConfigMessage, PromptConfig
import os
import datetime
from uuid import uuid4
from shared.helper.HelperFile import HelperFile
from collections import Counter
import fitz  # PyMuPDF
import base64
import re
import json
import hashlib


class Document():
    """Represents a single file to be ingested into the DMS.

    Encapsulates the full per-document pipeline: format conversion, text extraction
    (direct read or Vision LLM OCR), Markdown formatting, metadata extraction from
    path template and LLM, and tag extraction via LLM.

    Lifecycle:
        1. Instantiate with source file path and dependencies.
        2. Call ``await boot()`` — validates path metadata, converts the file,
           extracts and formats text, reads metadata and tags.
        3. Use getters (``get_content``, ``get_metadata``, ``get_tags``, etc.).
        4. Call ``cleanup()`` in a ``finally`` block to remove temporary files.
    """

    def __init__(self,
                 root_path: str,
                 source_file: str,
                 working_directory: str,
                 helper_config: HelperConfig,
                 llm_client: LLMClientInterface,
                 dms_client: DMSClientInterface,
                 path_template: str | None = None,
                 file_bytes: bytes = None,
                 file_hash: str | None = None,
                 ocr_client: OCRClientInterface | None = None,
                 prompt_client: PromptClientInterface | None = None) -> None:
        """Initialise the document without performing any I/O.

        Args:
            root_path: Root scan directory; used to compute the path relative to
                the template (e.g. ``/inbox``).
            source_file: Absolute path to the source file to ingest.
            working_directory: Base directory for temporary files.  A UUID-named
                subdirectory is created inside it during ``boot()``.
            helper_config: Shared configuration and logger provider.
            llm_client: LLM client used for Vision OCR, content formatting,
                metadata extraction, and tag extraction.
            dms_client: DMS client whose cache is read to provide existing
                document-type and tag names to LLM prompts.
            path_template: Path template string with ``{correspondent}``,
                ``{document_type}``, ``{year}``, ``{month}``, ``{day}``,
                ``{title}`` placeholders.  Defaults to ``{filename}``.
            file_bytes: Optional file content as bytes. If not passed it will be read automatically
            file_hash: Optional precomputed hash of the file content. If not passed it will be computed automatically during boot.
            ocr_client: Optional OCR client. If not passed, the document will be processed without OCR and vision LLM.
            prompt_client: Optional Prompt client. If not passed, the document will be processed without using the prompt client (e.g. for formatting, metadata and tag extraction).
        """
        # general
        self.logging = helper_config.get_logger()
        self._language = helper_config.get_string_val("LANGUAGE", "German")

        # settings
        self._skip_direct_read = helper_config.get_bool_val("DOC_INGESTION_SKIP_DIRECT_READ", False)
        self._skip_programmatic_read = helper_config.get_bool_val("DOC_INGESTION_SKIP_PROGRAMMATIC_READ", False)
        self._skip_vision_read = helper_config.get_bool_val("DOC_INGESTION_SKIP_VISION_READ", False)
        self._skip_ocr_read = helper_config.get_bool_val("DOC_INGESTION_SKIP_OCR_READ", False)

        self._minimum_text_chars_for_direct_read = helper_config.get_number_val("DOC_INGESTION_MINIMUM_TEXT_CHARS_FOR_DIRECT_READ", 40)
        self._page_dpi = helper_config.get_number_val("DOC_INGESTION_PAGE_DPI", 150)
        self._owner_company_name = helper_config.get_string_val("DOC_INGESTION_COMPANY_NAME", None)

        # files and paths
        self._root_path = root_path
        self._source_file = source_file
        self._path_template = path_template.strip()
        self._working_directory = os.path.join(working_directory, str(uuid4().hex[:8]))
        if not file_bytes:
            with open(source_file, "rb") as f:
                self._file_bytes = f.read()
        else:
            self._file_bytes = file_bytes

        if not file_hash:
            self._file_hash = hashlib.sha256(self._file_bytes).hexdigest()
        else:
            self._file_hash = file_hash

        # helper
        self._helper_config = helper_config
        self._helper_file = HelperFile()
        self._template_parser = PathTemplateParser(self._path_template, self._helper_config)

        # clients
        self._llm_client = llm_client
        self._dms_client = dms_client
        self._ocr_client = ocr_client
        self._prompt_client = prompt_client

        # bootable
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
        self._metadata_path: DocMetadata | None = None
        self._metadata_final: DocMetadata | None = None
        self._tags: list[str] | None = None

    ##########################################
    ############### CORE #####################
    ##########################################

    def is_booted(self) -> bool:
        """
        Return True if the working directory exists and the converter is ready to use.
        This is a sanity check to prevent starting the extraction process when the document is not properly initialized, which would lead to confusing errors later on.

        Returns:
            bool: True if the document is booted and ready for content extraction, False otherwise.
        """
        return self._helper_file.folder_exists(self._working_directory) and (self._converter is not None and self._converter.is_booted())

    def boot(self) -> None:
        """
        Phase 1: 
          - Validates the document path against the template
          - Creates the working directory
          - Converts the document to a processable format

        Raises:
            DocumentPathValidationError: If the document path does not match the template requirements (e.g. missing correspondent).
            RuntimeError: If the working directory cannot be created, if required dependencies are missing, or if conversion fails.
        """
        # Gate, if the path template does not match, throw error
        self._metadata_path = self._template_parser.convert_path_to_metadata(self._source_file, self._root_path)

        # create the working dir
        if not self._helper_file.create_folder(self._working_directory):
            raise RuntimeError("Failed to create working directory for Document in '%s'" % self._working_directory)

        # wrap in try to cleanup on any error during boot
        try:
            # check required dependencies
            if not self._llm_client.get_vision_model():
                raise RuntimeError("Document: LLM_MODEL_VISION not configured, cannot process documents")

            # load converter
            self._converter = DocumentConverter(
                helper_config=self._helper_config,
                working_directory=os.path.join(self._working_directory, "conversions")
            )
            self.logging.debug("Document converter initialized for file '%s'", self._source_file, color="green")

            # convert to processable format
            self._converted_file = self._converter.convert(self._source_file)
            self._converted_extension = self._helper_file.get_file_extension(self._converted_file, True, True)
            self._source_extension = self._helper_file.get_file_extension(self._source_file, True, True)
            self._source_filename = self._helper_file.get_basename(self._source_file, True)
            self.logging.debug("Document now in supported format: '%s'", self._converted_file, color="green")
        except Exception as e:
            self.cleanup()
            raise e

    async def load_content(self) -> None:
        """
        Loads the content by either reading directly or using the vision LLM.

        Raises:
            RuntimeError: If the document is not booted, or if content extraction fails/returned no content.
        """

        if not self.is_booted():
            raise RuntimeError("Document: cannot extract text, document not booted")

        # if direct read is enabled and usable
        if self._converted_extension in self._get_direct_read_file_formats() and not self._skip_direct_read:
            self._load_content_directly()

        # if OCR is enabled and usable
        elif self._ocr_client is not None and not self._skip_ocr_read:
            await self._load_content_ocr()

        # if vision llm is enabled and usable
        elif not self._skip_vision_read:
            await self._load_content_vision()

        # if programmatic read is enabled and usable
        elif self._source_extension in self._get_programmatic_read_file_formats() and not self._skip_programmatic_read:
            self._load_content_programmatic()

        # unprocessable wiht current configuration or file
        else:
            raise RuntimeError("Document: no valid content loading method available for file '%s' with current configuration" % self._source_filename)

        # if we got no content, we raise an error
        if not self._page_contents:
            raise RuntimeError("No content extracted from document '%s'" % self._converted_file)
        
    async def _load_content_ocr(self) -> None:
        """
        Reads the file by using ocr client
        Only usable if...
            - _skip_ocr_read is disabled        
        Falls back to vision reading -> programmatic reading -> direct reading
        Sets _page_contents, _content_needs_formatting and logs the extracted content.
        """
        # if invalid call
        if self._skip_ocr_read:
            self.logging.warning("Called _load_content_ocr with skip_ocr_read configuration! Falling back to vision reading...")
        if self._ocr_client is None:
            self.logging.warning("Called _load_content_ocr without an OCR client configured! Falling back to vision reading...")
        if self._skip_ocr_read or self._ocr_client is None:
            await self._load_content_vision()
            return

        self._page_contents = await self._read_file_ocr()
        self._content_needs_formatting = False
        if not self._page_contents:
            # if vision llm is deactivated, simply try to read the file programmatically
            self.logging.warning("OCR failed, falling back to vision reading...")
            await self._load_content_vision()

    async def _load_content_vision(self) -> None:
        """
        Reads the file by using vision llm
        Only usable if...
            - _skip_vision_read is disabled        
        Falls back to programmatic reading -> direct reading
        Sets _page_contents, _content_needs_formatting and logs the extracted content.
        """
        # if invalid call
        if self._skip_vision_read:
            self.logging.warning("Called _load_content_vision with _skip_vision_read configuration! Falling back to programmatic reading...")
            self._load_content_programmatic()
            return
        
        self._page_contents = await self._read_file_vision()
        self._content_needs_formatting = False
        if not self._page_contents:
            self.logging.warning("Vision failed, falling back to programmatic reading...")
            self._load_content_programmatic()

    def _load_content_programmatic(self) -> None:
        """
        Reads the file by using programmatic parsing (PyMuPDF)
        Only usable if...
            - the file format supports programmatic reading (e.g. pdf, docx)
            - _skip_programmatic_read is disabled        
        Falls back to direct reading
        Sets _page_contents, _content_needs_formatting and logs the extracted content.
        """
        # if invalid call
        unsupported = self._source_extension not in self._get_programmatic_read_file_formats()
        if unsupported:
            self.logging.warning("Called _load_content_programmatic for an unsupported file!")
        if self._skip_programmatic_read:
            self.logging.warning("Called _load_content_programmatic with _skip_programmatic_read configuration!")
        if unsupported or self._skip_programmatic_read:
            self._load_content_directly()
            return
        
        self._page_contents = self._read_file_programatically()  # already logs the content
        self._content_needs_formatting = True
        if not self._page_contents:
            self.logging.warning("Programmatic read failed, falling back to direct reading...")
            self._load_content_directly()
        
    def _load_content_directly(self) -> None:
        """
        Reads the file as plain text file
        Only usable if...
            - the file format supports direct reading (e.g. txt, md)
            - _skip_direct_read is disabled            
        Sets _page_contents, _content_needs_formatting and logs the extracted content.
        """
        # if invalid call
        unsupported = self._converted_extension not in self._get_direct_read_file_formats()
        if unsupported:
            self.logging.warning("Called _load_content_directly for an unsupported file!")
        if self._skip_direct_read:
            self.logging.warning("Called _load_content_directly with _skip_direct_read configuration!")
        if unsupported or self._skip_direct_read:
            self._page_contents = []
            self._content_needs_formatting = True
            return
        
        # read text file
        text = self._helper_file.read_text_file(self._converted_file)
        text = "" if text is None else text.strip()
        if not text:
            self.logging.error("Error reading text directly from file '%s'. Empty content", self._converted_file)
            self._page_contents = []
            self._content_needs_formatting = False
        else:
            self._page_contents = [text]
            self._content_needs_formatting = False
            self.logging.info("Read text directly from '%s'", self._source_filename, color="green")
            self.logging.debug(self._page_contents, color="blue")

    async def format_content(self) -> None:
        """Phase 2: merge pages (if needed), extract metadata and tags via Chat LLM.

        Must be called after a successful ``boot_extract``.  If ``boot_extract``
        produced raw pages (PDF + Vision path), merges them into the final
        Markdown content first.  Then extracts metadata and tags.

        Raises:
            RuntimeError: If content is not available or an LLM call fails.
        """
        # check if content was already extracted
        if not self._page_contents:
            raise RuntimeError("Cannot format content, no page contents available")

        # check if format is needed
        if not self._content_needs_formatting:
            self._final_content = "\n\n".join(self._page_contents)
            return

        # format each page
        formatted_pages: list[str] = []
        for idx, page in enumerate(self._page_contents):
            formatted = await self._call_chat_llm_format(page)

            # make sure there is some text on the page
            if len(formatted) >= self._minimum_text_chars_for_direct_read:
                formatted_pages.append(formatted)
                self.logging.info(f"Formatted text page {idx + 1}/{len(self._page_contents)} by Chat LLM from file '{self._source_filename}'", color="green")
                self.logging.debug(formatted, color="blue")
                continue
            else:
                raise RuntimeError(f"Formatted content from Chat LLM is too short or empty for page {idx + 1}/{len(self._page_contents)} of file '{self._source_filename}'")
        self.logging.info("Formatted pages from '%s'", self._source_filename, color="green")
        self.logging.debug(formatted_pages, color="blue")

        # merge the pages into the final content
        formatted_pages = self._remove_repeated_headers_footers(formatted_pages)
        formatted_pages = self._stitch_table_continuations(formatted_pages)
        self._final_content = await self._call_chat_llm_merge(formatted_pages)
        if not self._final_content.strip():
            self._final_content = None
            raise RuntimeError(f"Final merged content from Chat LLM is empty for file '{self._source_filename}'")
        self.logging.info("Merged Formatted pages from '%s'", self._source_filename, color="green")
        self.logging.debug(self._final_content, color="blue")

    async def load_metadata(self,
                            additional_doc_types: list[str] | None = None,
                            additional_correspondents: list[str] | None = None,
                            additional_tags: list[str] | None = None) -> None:
        """Phase 3: Fetch metadata from path and enrich them by using LLM on final_content

        Args:
            additional_doc_types: Optional list of additional document type strings to include as hints in the prompt for LLM metadata extraction.
            additional_correspondents: Optional list of additional correspondent strings to include as hints in the prompt for LLM metadata extraction.
            additional_tags: Optional list of additional tag name strings to include as hints in the prompt for LLM metadata extraction.
        Raises:
            RuntimeError: If content is not available or an LLM call fails.
        """
        # check if content was already extracted
        if not self._final_content:
            raise RuntimeError("Document: cannot extract metadata, no final content available")

        # check if path meta is available
        if not self._metadata_path:
            raise RuntimeError("Document: cannot extract metadata, no path metadata available. Did you ran boot()?")

        # fill up using llm
        llm_meta = await self._call_chat_llm_meta(
            additional_doc_types=additional_doc_types,
            additional_correspondents=additional_correspondents,
            additional_tags=additional_tags)
        # currently this is empty as we only extract tags from the content in the next phase,
        # but we prepare for the case that the LLM could already return
        # some tags in the metadata extraction step in the future
        llm_meta_tags = llm_meta.tags if llm_meta.tags else []
        # for cleaner code we separate the tags here
        path_tags = self._metadata_path.tags if self._metadata_path.tags else []

        # lets compare the tags (case insensitive) for avoid duplicates
        for tag in path_tags:
            if tag.lower() not in [t.lower() for t in llm_meta_tags]:
                llm_meta_tags.append(tag)
        # strip out empty lines
        llm_meta_tags = [t.strip() for t in llm_meta_tags if t.strip()]

        # merge path meta with llm meta (tags are processed later)
        content_meta = DocMetadata(
            correspondent=self._metadata_path.correspondent or llm_meta.correspondent,
            document_type=self._metadata_path.document_type or llm_meta.document_type,
            year=self._metadata_path.year or llm_meta.year,
            month=self._metadata_path.month or llm_meta.month,
            day=self._metadata_path.day or llm_meta.day,
            title=self._metadata_path.title or llm_meta.title,
            tags=llm_meta_tags,
            filename=self._helper_file.get_basename(self._source_file, True)
        )

        # fallback: if year/month/day is missing, use file modification date
        fallback_applied = False
        file_mtime = os.path.getmtime(self._source_file)
        file_date = datetime.datetime.fromtimestamp(file_mtime)
        if not content_meta.year:
            content_meta.year = str(file_date.year)
            self.logging.info("Year missing — using file modification date year '%s' for '%s'", content_meta.year, self._source_filename, color="magenta")
            fallback_applied = True
        if not content_meta.month:
            content_meta.month = f"{file_date.month:02d}"
            self.logging.info("Month missing — using file modification date month '%s' for '%s'", content_meta.month, self._source_filename, color="magenta")
            fallback_applied = True
        if not content_meta.day:
            content_meta.day = f"{file_date.day:02d}"
            self.logging.info("Day missing — using file modification date day '%s' for '%s'", content_meta.day, self._source_filename, color="magenta")
            fallback_applied = True

        # fallback: if title is empty, use filename without extension
        if not content_meta.title:
            content_meta.title = self._helper_file.get_basename(self._source_file, False)
            self.logging.info("Title missing — using filename '%s' as title", content_meta.title, color="magenta")
            fallback_applied = True

        # fallback: if document_type is empty, use generic placeholder
        if not content_meta.document_type:
            content_meta.document_type = "Unknown Document Type"
            self.logging.info("Document type missing — using fallback 'Unknown Document Type' for '%s'", self._source_filename, color="magenta")
            fallback_applied = True

        # if fallback was applied, add tag "Todo"
        if fallback_applied and "todo" not in [t.lower() for t in content_meta.tags]:
            content_meta.tags.append("Todo")

        # make sure each field of DocMetadata is filled (skip list fields — empty list is valid)
        skip_fields = {"tags", "quarter"}
        for field_name in content_meta.__dataclass_fields__.keys():
            if field_name in skip_fields:
                continue
            if not getattr(content_meta, field_name):
                raise RuntimeError("'%s' is missing after LLM extraction" % (field_name))
        self._metadata_final = content_meta
        # log as dict for better readability in logs
        self.logging.info("Metadata loaded for '%s'", self._source_filename, color="green")
        self.logging.debug(self._metadata_final.__dataclass_fields__, color="blue")

    async def load_tags(self, additional_tags: list[str] | None = None) -> None:
        """
        Phase 4: Fetch tags from the final content using Chat LLM.

        Args:
            additional_tags: Optional list of additional tag name strings to include as hints in the prompt.

        Raises:
            RuntimeError: If content is not available or an LLM call fails.
        """
        # check if content was already extracted
        if not self._final_content:
            raise RuntimeError("Document: cannot extract tags, no final content available")

        # use current tags if metadata extraction already found some
        current_tags = self._metadata_final.tags if self._metadata_final and self._metadata_final.tags else []
        # lets compare given additional tags with current tags (case insensitive) for avoid duplicates
        for tag in additional_tags or []:
            if tag.lower() not in [t.lower() for t in current_tags]:
                current_tags.append(tag)
        # strip out empty lines
        current_tags = [t.strip() for t in current_tags if t.strip()]
        # if empty, set to None, because its the default
        if not current_tags:
            current_tags = None

        # fetch tags via LLM
        tags = await self._call_chat_llm_tags(additional_tags=current_tags)
        # prepend tags collected from the elastic zone of the path template
        path_tags = self._metadata_path.tags if self._metadata_path else []
        combined = list(path_tags)
        for tag in tags:
            if tag not in combined:
                combined.append(tag)
        self._tags = combined
        self.logging.info("Tags loaded for '%s'", self._source_filename, color="green")
        self.logging.debug(self._tags, color="blue")

    def cleanup(self) -> None:
        """
        Remove the working directory and reset all bootable vars.

        Must be called in a ``finally`` block after ``boot()`` to ensure
        temporary files are deleted even when ingestion fails.
        Safe to call even if ``boot()`` was never called or failed midway.
        """
        # delete the working dir
        if not self._helper_file.remove_folder(self._working_directory):
            self.logging.warning(f"DocHelper: failed to delete working directory '{self._working_directory}' for converted files")
        # reset bootable
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
        self._metadata_path: DocMetadata | None = None
        self._metadata_final: DocMetadata | None = None
        self._tags: list[str] | None = None

    ##########################################
    ############### GETTER ###################
    ##########################################

    def get_source_file(self, filename_only: bool = False) -> str | None:
        """Return the original source file path or just the filename."""
        if not self.is_booted():
            raise RuntimeError("Document: cannot get source file, document not booted")
        if filename_only:
            return self._source_filename
        return self._source_file

    def _get_direct_read_file_formats(self) -> list[str]:
        """Return a list of file extensions that can be read directly without page iteration. E.g., 'txt', 'md'..."""
        return ["txt", "md"]
    
    def _get_programmatic_read_file_formats(self) -> list[str]:
        """Return a list of file extensions that can be read programmatically. E.g., 'pdf', 'docx'..."""
        return ["pdf", "txt", "md", "docx", "doc", "odt", "ott", "xlsx", "xls", "ods", "csv", "pptx", "ppt", "odp", "rtf"]

    def get_title(self) -> str:
        """Return the document title as '{correspondent} {document_type} {DD.MM.YYYY}'."""
        return f"{self._metadata_final.correspondent} {self._metadata_final.document_type} {self._metadata_final.day}.{self._metadata_final.month}.{self._metadata_final.year}"

    def get_metadata(self) -> DocMetadata:
        """Return the fully merged metadata (path template + LLM fill-in)."""
        return self._metadata_final

    def get_tags(self) -> list[str]:
        """Return the LLM-extracted tag list, or an empty list if none were found."""
        return self._tags or []

    def get_content(self) -> str:
        """Return the Markdown-formatted document content."""
        return self._final_content

    def get_date_string(self, pattern: str = "%Y-%m-%d") -> str | None:
        """Return the document creation date as a string in the given format, or None if not available."""
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
        """Return the original file content as bytes."""
        return self._file_bytes

    def get_file_hash(self) -> str:
        """Return the precomputed hash of the file content."""
        return self._file_hash

    ##########################################
    ########### CONTENT READER ###############
    ##########################################

    def _read_file_programatically(self) -> list[str]:
        """
        Uses PyMuPDF to extract text from each page
        Ignores empty pages or pages with less minimum text chars.

        Returns:
            List of extracted page texts.
        """
        page_texts: list[str] = []
        try:
            # open the document
            doc = fitz.open(self._converted_file)

            # iterate pages
            for page_num, page in enumerate(doc):
                direct_text = page.get_text().strip()

                # make sure there is some text on the page
                if len(direct_text) >= self._minimum_text_chars_for_direct_read:
                    page_texts.append(direct_text)
                    self.logging.info(f"Extracted text page {page_num + 1}/{len(doc)} programmatically from file '{self._source_filename}'", color="green")
                    self.logging.debug(direct_text, color="blue")
                    continue
                else:
                    self.logging.info(f"No text extracted programmatically from page {page_num + 1}/{len(doc)} of file '{self._source_filename}'", color="yellow")
            doc.close()
        except Exception as e:
            self.logging.error("Error extracting text programmatically from file '%s': %s", self._converted_file, e)
            return []
        return page_texts

    async def _read_file_vision(self) -> list[str]:
        """
        Uses Vision-LLM to extract text from each page
        Ignores empty pages or pages with less minimum text chars.

        Returns:
            List of extracted page texts.
        """
        page_texts: list[str] = []
        vision_context_chars = self._helper_config.get_number_val("DOC_INGESTION_VISION_CONTEXT_CHARS", 300)    
        try:
            doc = fitz.open(self._converted_file)
            for page_num, page in enumerate(doc):
                # convert page to image as base64 for LLM input
                pix = page.get_pixmap(dpi=self._page_dpi)
                png_bytes = pix.tobytes("png")
                b64 = base64.b64encode(png_bytes).decode("ascii")

                # add some context of the previous page for helping the model to understand if there is a continuation of a table or section
                context_before = page_texts[-1][-vision_context_chars:] if page_texts else ""

                # read the content using vision llm
                text = await self._call_vision_llm(b64, context_before)

                # make sure there is some text on the page
                if len(text) >= self._minimum_text_chars_for_direct_read:
                    page_texts.append(text)
                    self.logging.info(f"Extracted text page {page_num + 1}/{len(doc)} by vision LLM from file '{self._source_filename}'", color="green")
                    self.logging.debug(text, color="blue")
                    continue
                else:
                    self.logging.info(f"No text extracted by vision LLM from page {page_num + 1}/{len(doc)} of file '{self._source_filename}' --> Falling back to direct reading", color="yellow")
                    # if there is NO content found on the page, retry it with direct reading
                    direct_text = page.get_text().strip()
                    # make sure there is some text on the page
                    if len(direct_text) >= self._minimum_text_chars_for_direct_read:
                        page_texts.append(direct_text)
                        self.logging.info(f"Extracted text page {page_num + 1}/{len(doc)} by direct reading from file '{self._source_filename}'", color="green")
                        self.logging.debug(direct_text, color="blue")
                        continue
                    else:
                        self.logging.info(f"No text extracted by fallback direct reading from page {page_num + 1}/{len(doc)} of file '{self._source_filename}'", color="yellow")
            doc.close()
        except Exception as e:
            self.logging.error("Error extracting text by vision LLM from file '%s': %s", self._source_filename, e)
            return []
        return page_texts

    async def _read_file_ocr(self) -> list[str]:
        """
        Uses the OCR client (e.g. Docling) to extract Markdown text from the document.

        Returns:
            list[str]: a single-element list containing the complete document Markdown.
        """
        try:
            with open(self._converted_file, "rb") as f:
                file_bytes = f.read()
            filename = self._helper_file.get_basename(self._converted_file, with_extension=True)
            markdown = await self._ocr_client.do_convert_pdf_to_markdown(file_bytes, filename)
            if not markdown or not markdown.strip():
                self.logging.error("Error extracting text by OCR client %s from file '%s'. Empty content", self._ocr_client.get_engine_name(), self._source_filename)
                return []
            self.logging.info("Extracted text by OCR client %s from file '%s'", self._ocr_client.get_engine_name(), self._source_filename, color="green")
            self.logging.debug(markdown, color="blue")
            return [markdown]
        except Exception as e:
            self.logging.error("Error extracting text by OCR client %s from file '%s': %s", self._ocr_client.get_engine_name(), self._source_filename, e)
            return []

    ##########################################
    ############ CONTENT FORMATTER ###########
    ##########################################

    def _remove_repeated_headers_footers(self, formatted_pages: list[str]) -> list[str]:
        """
        Checks if there are lines at the start or end of the pages which are repeated across most pages. E.g. a header with the document title, or a footer with page numbers. 
        If such lines are found, they are stripped from the respective page boundaries to produce cleaner final content.

        Args:
            formatted_pages: Formatted text of each page as a list of strings.

        Returns:
            Cleaned pages with repeated boundary lines stripped.
        """
        if len(formatted_pages) < 2:
            return formatted_pages

        # count occurrences of each line in the first and last 3 lines of all pages
        header_counts: Counter = Counter()
        footer_counts: Counter = Counter()
        for page in formatted_pages:
            lines = [l for l in page.splitlines() if l.strip()]
            for line in lines[:3]:
                header_counts[line.strip()] += 1
            for line in lines[-3:]:
                footer_counts[line.strip()] += 1

        # identify lines that appear in ≥ 60 % of pages as repeated headers/footers
        threshold = max(2, len(formatted_pages) * 0.6)
        repeated_headers = {line for line, count in header_counts.items() if count >= threshold}
        repeated_footers = {line for line, count in footer_counts.items() if count >= threshold}

        # if nothing to remove, return original pages
        if not repeated_headers and not repeated_footers:
            return formatted_pages

        self.logging.debug(
            "Removing repeated headers %s and footers %s for '%s'",
            repeated_headers, repeated_footers, self._source_filename
        )

        # strip lines that match the repeated headers/footers from the start/end of each page
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
        Iterates all pages and check if the following page starts with a table while the current page ends with a table.
        If so, we merge the two pages without adding a newline, so that tables that are split across pages are merged back together in the final content.

        Args:
            formatted_pages: Per-page Markdown strings (after header/footer cleanup).

        Returns:
            Pages list with cross-page tables merged into single entries.
        """
        # iterate all pages
        new_pages: list[str] = []
        i = 0
        while i < len(formatted_pages):
            new_page = formatted_pages[i]
            # if the current page ends with a table and the next page starts with a table...
            while (
                i + 1 < len(formatted_pages)
                and self._page_ends_with_table(new_page)
                and self._page_starts_with_table(formatted_pages[i + 1])
            ):
                i += 1
                # merge the next page into the current one without adding a newline, so that tables split across pages are joined together
                new_page = new_page.rstrip() + "\n" + formatted_pages[i].lstrip()
                self.logging.debug(
                    "Stitched cross-page table at boundary %d for '%s'", i, self._source_filename
                )
            # add the (possibly merged) current page to the result and move to the next one
            new_pages.append(new_page)
            i += 1
        return new_pages

    def _page_ends_with_table(self, page: str) -> bool:
        """
        Checks if the last non-empty line of the page starts with a pipe '|' and there are at least 2 pipe characters in that line.
        This would be a strong indicator that it's part of a Markdown table.

        Args:
            page: The Markdown content of the page.

        Returns:
            True if the page likely ends with a table, False otherwise.
        """
        last = next((l for l in reversed(page.splitlines()) if l.strip()), "")
        stripped = last.strip()
        return stripped.startswith("|") and stripped.count("|") >= 2

    def _page_starts_with_table(self, page: str) -> bool:
        """
        Checks if the first non-empty line of the page starts with a pipe '|' and there are at least 2 pipe characters in that line.
        This would be a strong indicator that it's part of a Markdown table.

        Args:
            page: The Markdown content of the page.

        Returns:
            True if the page likely starts with a table, False otherwise.
        """
        first = next((l for l in page.splitlines() if l.strip()), "")
        first = first.strip()
        return first.startswith("|") and first.count("|") >= 2

    ##########################################
    ################# LLM ####################
    ##########################################

    async def _call_vision_llm(self, png_b64_data: str, context_before: str) -> str:
        """
        Send an image to the Vision LLM and return its content.

        Args:
            png_b64_data: Base64-encoded PNG of the page.
            context_before: Trailing text of the previously formatted page (may be empty).

        Returns:
            str: The text content extracted by the Vision LLM, or an empty string if the call fails.
        """    
        try:
            # get the prompt messages
            prompt = await self._get_prompt_vision_ocr(image_bytes=png_b64_data, context=context_before)

            # run the prompt messages through the LLM
            result = await self._llm_client.do_chat_vision(prompt["messages"])
            result = re.sub(r"```[a-zA-Z]*\s*\n?(.*?)\n?```", r"\1", result.strip(), flags=re.DOTALL)
            return result.strip()
        except Exception as e:
            self.logging.error("Call Vision LLM %s failed for '%s': %s", self._llm_client.get_vision_model(), self._source_filename, e)
            return ""

    async def _call_chat_llm_format(self, raw: str) -> str:
        """
        Send raw text to the Chat LLM and return its formatted content.

        Args:
            raw: Plain text as returned by ``_extract_text()``.

        Returns:
            str: The text content formatted by the Chat LLM, or an empty string if the call fails.
        """
        try:
            # get the prompt messages
            prompt = await self._get_prompt_format(unformatted_text=raw)

            # run the prompt messages through the LLM
            result = await self._llm_client.do_chat(prompt["messages"])
            
            # Strip any code fences the model may wrap output in
            result = re.sub(r"```[a-zA-Z]*\s*\n?(.*?)\n?```", r"\1", result.strip(), flags=re.DOTALL)
            return result.strip()
        except Exception as e:
            self.logging.error("Call Chat LLM %s for formatting failed for '%s': %s", self._llm_client.get_chat_model(), self._source_filename, e)
            return ""

    async def _call_chat_llm_merge(self, formatted_pages: list[str]) -> str:
        """
        Detect and join boundaries where content flows across pages.

        Builds a compact overview of each page boundary (last 5 + first 5 non-empty
        lines of adjacent pages) and asks the chat LLM which boundaries represent
        a mid-flow break (unfinished sentence, continuing list item, etc.) rather
        than a natural section end.  Only those boundaries are joined without a
        blank-line separator; all others keep the standard paragraph gap.

        Falls back to a plain ``\\n\\n`` join if the LLM call or JSON parse fails.

        Args:
            formatted_pages: Per-page Markdown strings (after programmatic cleanup).

        Returns:
            Final assembled Markdown document string.
        """
        #if there is only one page, return it as the final content
        if len(formatted_pages) == 1:
            return formatted_pages[0]

        merge_boundaries: set[int] = set()
        try:
            # get the prompt messages
            prompt = await self._get_prompt_merge(pages=formatted_pages)

            # run the prompt messages through the LLM
            raw = await self._llm_client.do_chat(prompt["messages"])

            # parse and validate the response json
            data = self._prompt_client.validate_prompt_schema(prompt["config"], raw)
            if data is None:
                raise ValueError(f"LLM response did not match expected schema!")
            merge_boundaries = set(int(b) for b in (data.get("merge_at_boundaries") or []))
        except Exception as e:
            self.logging.warning("Call Chat LLM %s for merging failed for '%s': %s.\nContinue joining pages as-is.", self._llm_client.get_chat_model(), self._source_filename, e, color="yellow")

        # join the merged contents. If no boundaries to merge, this simply rejoins the original list with double newlines, preserving the original page breaks.
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

    async def _call_chat_llm_meta(self,
                                  additional_doc_types: list[str] | None = None,
                                  additional_correspondents: list[str] | None = None,
                                  additional_tags: list[str] | None = None) -> DocMetadata:
        """Extract metadata from the formatted document content via the chat LLM.

        Sends the first 3 000 characters of the formatted content together with
        an extraction prompt and an optional hint containing existing DMS
        document-type names, correspondents, and tags.  Parses the JSON response into a ``DocMetadata``.

        Args:
            additional_doc_types: Optional list of additional document type strings to include as hints in the prompt.
            additional_correspondents: Optional list of additional correspondent strings to include as hints in the prompt.
            additional_tags: Optional list of additional tag name strings to include as hints in the prompt.

        Returns:
            ``DocMetadata`` with fields populated from the LLM response.

        Raises:
            RuntimeError: If the LLM call fails or the response cannot be
                parsed as JSON.
        """
        try:
            # get the prompt messages
            prompt = await self._get_prompt_extraction(
                additional_doc_types=additional_doc_types,
                additional_correspondents=additional_correspondents,
                additional_tags=additional_tags)

            # run the prompt messages through the LLM
            raw = await self._llm_client.do_chat(prompt["messages"])

            # parse and validate the response json
            data = self._prompt_client.validate_prompt_schema(prompt["config"], raw)
            if data is None:
                raise ValueError(f"LLM response did not match expected schema!")
            
            return DocMetadata(
                correspondent=data.get("correspondent") or None,
                document_type=data.get("document_type") or None,
                year=data.get("year") or None,
                month=data.get("month") or None,
                day=data.get("day") or None,
                title=data.get("title") or None,
                filename=self._helper_file.get_basename(self._source_file, True))
        except Exception as e:
            raise RuntimeError(f"Failed to read meta from content using llm '{self._source_file}': {e}")

    async def _call_chat_llm_tags(self, additional_tags: list[str] | None = None) -> list[str]:
        """
        Extract tags from the formatted document content via the chat LLM.

        Sends the first 3 000 characters of the formatted content together with
        a tagging prompt that includes existing DMS tag names as hints.  The
        model returns a JSON array of at most 3 tag name strings.

        Args:
            additional_tags: Optional list of additional tag name strings to include as hints in the prompt.

        Returns:
            list[str]: List of tag name strings (is never empty).

        Raises:
            RuntimeError: If the LLM call fails or the response is not a valid JSON array or empty.
        """
        try:
            # get the prompt messages
            prompt = await self._get_prompt_tags(additional_tags=additional_tags)

            # run the prompt messages through the LLM
            raw = await self._llm_client.do_chat(prompt["messages"])

            # parse and validate the response json
            data = self._prompt_client.validate_prompt_schema(prompt["config"], raw)
            if data is None:
                raise ValueError(f"LLM response did not match expected schema!")
            
            # if empty throw error
            if not data["tags"]:
                raise ValueError(f"Tag extraction LLM response is an empty list for file '{self._source_file}'")
            return data["tags"]
        except Exception as e:
            raise RuntimeError(f"Failed to read tags from content using llm '{self._source_file}': {e}")

    ##########################################
    ################# DMS ####################
    ##########################################

    def _get_dms_cache(self) -> dict[str, list[str]]:
        """Read document type and tag names from DMS cache to provide context for the LLM."""
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
        Fetches a list of strings from the DMS cache for the given key (e.g., "document_types", "tags", "correspondents") 
        and merges it with an optional list of additional strings, ensuring no duplicates (case-insensitive) in the final list.

        Args:
            key: The cache key to fetch (e.g., "document_types", "tags", "correspondents").
            additionals: Optional list of additional strings to merge with the cached values.

        Returns:
            A merged list of strings from the cache and the additional list, without duplicates.
        """
        additionals = [] if additionals is None else additionals
        cache = self._get_dms_cache()
        cache_elements = cache.get(key, []) if cache else []
        cache_elements_lower = [element.lower() for element in cache_elements]
        if additionals:
            # add each additional element if not already in the list, to avoid duplicates
            # we compare case-insensitive to avoid duplicates with different capitalizations, but we keep the original capitalization in the final list
            for element in additionals:
                if element.lower() not in cache_elements_lower:
                    cache_elements.append(element)
        return cache_elements

    ##########################################
    ############### PROMPTS ##################
    ##########################################

    async def _get_prompt_extraction(self,
        additional_doc_types: list[str] | None = None,
        additional_correspondents: list[str] | None = None,
        additional_tags: list[str] | None = None) -> dict[str, any]:
        """
        Uses the prompt client to fetch and render the metadata extraction prompt, including existing DMS values as hints.

        Args:
            additional_doc_types (list[str] | None): Optional list of additional document type strings to include as hints in the prompt.
            additional_correspondents (list[str] | None): Optional list of additional correspondent strings to include as hints in the prompt.
            additional_tags (list[str] | None): Optional list of additional tag name strings to include as hints in the prompt.

        Returns:
            dict[str, any]: Dict with keys config (PromptConfig) and messages (dicts with "role" and "content") ready to be sent to the chat LLM.
        """
        # Read existing values
        doc_types = self._get_from_cache("document_types", additional_doc_types)
        correspondents = self._get_from_cache("correspondents", additional_correspondents)
        tags = self._get_from_cache("tags", additional_tags)

        # prepare replacements
        replacements = {
            "company": self._owner_company_name,
            "language": self._language,
            "document_content": self._final_content,
            "existing_document_types": ", ".join(doc_types) if doc_types else "(none)",
            "existing_correspondents": ", ".join(correspondents) if correspondents else "(none)",
            "existing_tags": ", ".join(tags) if tags else "(none)"
        }
        # fetch prompt config and render the template
        prompt = await self._prompt_client.do_fetch_prompt(id="doc_ingestion_extract_metadata")
        messages = self._prompt_client.render_prompt(
            prompt=prompt,
            replacements=replacements,
            max_chars=self._llm_client.get_chat_model_max_chars())
        # convert to dict and return
        dict_messages = [{"role": m.role, "content": m.content} for m in messages]
        return {
            "config": prompt,
            "messages": dict_messages
        }

    async def _get_prompt_tags(self, additional_tags: list[str] | None = None) -> dict[str, any]:
        """
        Uses the prompt client to fetch and render the tags extraction prompt, including existing DMS values as hints.

        Args:
            additional_tags (list[str] | None): Optional list of additional tag name strings to include as hints in the prompt.

        Returns:
            dict[str, any]: Dict with keys config (PromptConfig) and messages (dicts with "role" and "content") ready to be sent to the chat LLM.
        """
        # Read existing values
        tags = self._get_from_cache("tags", additional_tags)

        # prepare replacements
        replacements = {
            "language": self._language,
            "document_content": self._final_content,
            "existing_tags": ", ".join(tags) if tags else "(none)"
        }
        # fetch prompt config and render the template
        prompt = await self._prompt_client.do_fetch_prompt(id="doc_ingestion_extract_tags")
        messages = self._prompt_client.render_prompt(
            prompt=prompt,
            replacements=replacements,
            max_chars=self._llm_client.get_chat_model_max_chars())
        # convert to dict and return
        dict_messages = [{"role": m.role, "content": m.content} for m in messages]
        return {
            "config": prompt,
            "messages": dict_messages
        }

    async def _get_prompt_vision_ocr(self, image_bytes:str, context: str = "") -> dict[str, any]:
        """
        Uses the prompt client to fetch and render the vision OCR prompt, including previous context as hints.

        Args:
            image_bytes (str): Base64-encoded string of the image to be processed by the vision OCR.
            context (str): Optional string of previous context to include as hints in the prompt.

        Returns:
            dict[str, any]: Dict with keys config (PromptConfig) and messages (dicts with "role" and "content") ready to be sent to the vision LLM.

        Raises:
            ValueError: If the prompt template does not contain at least one user message to attach the image to.
        """       
        # prepare replacements
        replacements = {
            "previous_context": context if context else "N/A"
        }
        # fetch prompt config and render the template
        prompt = await self._prompt_client.do_fetch_prompt(id="doc_ingestion_vision_ocr")
        messages = self._prompt_client.render_prompt(
            prompt=prompt,
            replacements=replacements)
        # convert to dict and return
        dict_messages = [{"role": m.role, "content": m.content} for m in messages]

        # iterate backwards the messages until the first user message is found. If found, add image-key
        image_appended = False
        for m in reversed(dict_messages):
            if m["role"] == "user":
                m["images"] = [image_bytes]
                image_appended = True
                break

        # if the image was not appended, something is wrong with the prompt template. Raise error
        if not image_appended:
            raise ValueError("Vision OCR prompt template must contain at least one user message to attach the image to. Please check the prompt configuration for 'doc_ingestion_vision_ocr'.")

        return {
            "config": prompt,
            "messages": dict_messages
        }

    async def _get_prompt_format(self, unformatted_text: str) -> dict[str, any]:
        """
        Uses the prompt client to fetch and render the format prompt, including the unformatted text as input.

        Args:
            unformatted_text (str): The raw text of the document to be formatted.

        Returns:
            dict[str, any]: Dict with keys config (PromptConfig) and messages (dicts with "role" and "content") ready to be sent to the chat LLM.
        """
        # prepare replacements
        replacements = {
            "raw_content": unformatted_text
        }
        # fetch prompt config and render the template
        prompt = await self._prompt_client.do_fetch_prompt(id="doc_ingestion_document_formatter")
        messages = self._prompt_client.render_prompt(
            prompt=prompt,
            replacements=replacements)
        
        # count the length in chars. If it exceeds the model max, throw error
        total_length = sum(len(m.content) for m in messages)
        if total_length > self._llm_client.get_chat_model_max_chars():
            raise ValueError(f"Formatted prompt messages exceed the maximum allowed characters for the chat model ({total_length} > {self._llm_client.get_chat_model_max_chars()}). Consider reducing the input text or increasing the model max chars if possible.")

        # convert to dict and return
        dict_messages = [{"role": m.role, "content": m.content} for m in messages]
        return {
            "config": prompt,
            "messages": dict_messages
        }

    async def _get_prompt_merge(self, pages: list[str]) -> dict[str, any]:
        """
        Uses the prompt client to fetch and render the merge prompt, including the pages as boundary-inputs.

        Args:
            pages (list[str]): The list of page texts to be merged.

        Returns:
            dict[str, any]: Dict with keys config (PromptConfig) and messages (dicts with "role" and "content") ready to be sent to the chat LLM.
        """
        boundary_snippets: list[str] = []
        for i in range(len(pages) - 1):
            lines_a = [l for l in pages[i].splitlines() if l.strip()]
            lines_b = [l for l in pages[i + 1].splitlines() if l.strip()]
            # take last 5 non-empty lines of page A and first 5 non-empty lines of page B for context
            tail_a = "\n".join(lines_a[-5:])
            head_b = "\n".join(lines_b[:5])

            boundary_snippets.append(
                "--- Boundary %d→%d ---\nEnd of page %d:\n%s\nStart of page %d:\n%s"
                % (i, i + 1, i, tail_a, i + 1, head_b)
            )

        # prepare replacements
        replacements = {
            "boundary_snippets": "\n\n".join(boundary_snippets)
        }
        # fetch prompt config and render the template
        prompt = await self._prompt_client.do_fetch_prompt(id="doc_ingestion_merge")
        messages = self._prompt_client.render_prompt(
            prompt=prompt,
            replacements=replacements,
            max_chars=self._llm_client.get_chat_model_max_chars())
        
        # convert to dict and return
        dict_messages = [{"role": m.role, "content": m.content} for m in messages]
        return {
            "config": prompt,
            "messages": dict_messages
        }
