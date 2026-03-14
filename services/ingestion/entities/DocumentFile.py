import os

from shared.helper.HelperConfig import HelperConfig
from services.ingestion.entities.DocumentInterface import DocumentInterface
from services.ingestion.helper.DocumentConverter import DocumentConverter
from services.ingestion.helper.PathTemplateParser import PathTemplateParser
from services.ingestion.dataclasses import DocMetadata
from shared.clients.llm.LLMClientInterface import LLMClientInterface
from shared.clients.dms.DMSClientInterface import DMSClientInterface
from shared.clients.ocr.OCRClientInterface import OCRClientInterface
from shared.clients.prompt.PromptClientInterface import PromptClientInterface


class DocumentFile(DocumentInterface):
    """
    Implementation for FileIngestion
    """

    def __init__(self,
                 root_path: str,
                 source_file: str,
                 working_directory: str,
                 helper_config: HelperConfig,
                 llm_client: LLMClientInterface,
                 dms_client: DMSClientInterface,
                 path_template: str | None = None,
                 file_bytes: bytes | None = None,
                 file_hash: str | None = None,
                 ocr_client: OCRClientInterface | None = None,
                 prompt_client: PromptClientInterface | None = None) -> None:
        """
        Initialise a FileDocument

        Args:
            root_path: Root scan directory; used to compute the path relative to the path-template
            source_file: Absolute path to the source file to ingest.
            working_directory: Temp directory for files
            helper_config: Shared configuration and logger provider.
            llm_client: LLM client for Vision OCR, formatting, metadata, and tag extraction.
            dms_client: DMS client whose cache supplies hints for LLM prompts.
            path_template: Path template to validate the source file against and extract metadata from.
            file_bytes: Optional pre-read file content. Read from disk when not supplied.
            file_hash: Optional precomputed SHA-256 hex digest. Computed automatically when omitted.
            ocr_client: Optional OCR client for Docling-style conversion.
            prompt_client: Optional Prompt client for named template rendering.
        """
        # init super
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

        # file-specific fields
        self._root_path = root_path
        self._template_parser = PathTemplateParser(path_template.lstrip(os.sep).rstrip(os.sep).strip(), self._helper_config)

    ##########################################
    ############# LIFECYCLE ##################
    ##########################################

    def _before_boot(self) -> None:
        """
        Validate the source file's path against the path template; raises ``DocumentPathValidationError`` immediately on mismatch 
        so the caller can skip the file without spinning up expensive resources.

        Raises:
            DocumentPathValidationError: If the path does not satisfy the template requirements (e.g. missing correspondent segment).
        """

        # Validates the source_file path against the path template and raises error if it doesn't match
        self._metadata_path = self._template_parser.convert_path_to_metadata(self._source_file, self._root_path)     

    def _before_cleanup(self) -> None:
        self._metadata_path = None   
        
    ##########################################
    ############## GETTER ####################
    ##########################################
    
    def _get_type_name(self) -> str:
        return "File"

    ##########################################
    ################ META ####################
    ##########################################

    def _get_fallback_metadata(self) -> DocMetadata:
        # empty since there is no fallback
        return DocMetadata(filename=self._helper_file.get_basename(self._source_file, True))

    def _get_additional_metadata(self) -> DocMetadata:
        """
        Returns the metadata from PathTemplateParser initialized in _before_boot.

        Returns:
            DocMetadata: The metadata with any template-derived fields populated.
        """
        # the stored value may be None before boot() — the ABC handles that case
        return self._metadata_path
