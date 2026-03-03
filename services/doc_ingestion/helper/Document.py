from shared.helper.HelperConfig import HelperConfig
from services.doc_ingestion.helper.DocumentConverter import DocumentConverter
from shared.clients.llm.LLMClientInterface import LLMClientInterface
from shared.clients.dms.DMSClientInterface import DMSClientInterface
import os
from uuid import uuid4
from shared.helper.HelperFile import HelperFile
import fitz # PyMuPDF
import base64
import re
import json 
from dataclasses import dataclass

@dataclass
class DocMetadata:
    correspondent: str | None = None
    document_type: str | None = None
    year: str | None = None
    month: str | None = None
    day: str | None = None
    title: str | None = None
    filename: str | None = None

class Document():
    """Represents a document to be ingested, along with its metadata."""
    def __init__(self, 
                 root_path:str,
                 source_file:str, 
                 working_directory:str, 
                 helper_config: HelperConfig, 
                 llm_client: LLMClientInterface,
                 dms_client: DMSClientInterface,
                 path_template: str | None = None) -> None:
        self._root_path = root_path
        self._path_template = path_template or self._get_default_path_template()
        self._source_file = source_file
        self._config = helper_config
        self.logging = helper_config.get_logger()
        self._working_directory = os.path.join(working_directory, str(uuid4().hex[:8]))
        self._helper_file = HelperFile()
        self._llm_client = llm_client
        self._dms_client = dms_client
        self._language = self._config.get_string_val("LANGUAGE", "German")
        self._converter: DocumentConverter | None = None  
        self._converted_file:str | None = None 
        self._extension:str | None = None
        self._metadata:DocMetadata|None = None
        self._tags:list[str]|None = None

    ##########################################
    ############### CORE #####################
    ##########################################

    async def boot(self) -> None:
        #create directory
        if not self._helper_file.create_folder(self._working_directory):
            raise RuntimeError(f"Failed to create working directory for Document in '{self._working_directory}'")
        
        #use try for making sure we clean up if anything goes wrong during boot, to avoid leaving temp files around
        try:
            #make sure we have access to Vision LLM if needed
            if not self._llm_client.get_vision_model():
                raise RuntimeError("Document: LLM_MODEL_VISION not configured, cannot process documents that require OCR")
            
            #init converter
            self._converter = DocumentConverter(
                helper_config=self._config,
                working_directory=os.path.join(self._working_directory, "conversions")
            )
            self.logging.debug("Document converter initialized for file '%s'", self._source_file, color="green")
                    
            #convert the file to supported format
            self._converted_file = self._converter.convert(self._source_file)
            self._extension = self._helper_file.get_file_extension(self._converted_file, True, True)
            self.logging.debug("Document now in supported format: '%s'", self._converted_file, color="green")

            #read the content and metadata of the file
            self._content = await self._extract_text()  
            self._metadata = await self._read_metadata()
            self._tags = await self._read_tags_from_content()
        except Exception as e:
            self._helper_file.remove_folder(self._working_directory)
            raise e
        

    def cleanup(self) -> None:
        #delete the working dir
        if not self._helper_file.remove_folder(self._working_directory):
            self.logging.warning(f"DocHelper: failed to delete working directory '{self._working_directory}' for converted files")
        #reset state
        self._converted_file = None
        self._extension = None
        self._content = None
        self._metadata = None
        self._tags = None

    def is_booted(self) -> bool:
        return self._helper_file.folder_exists(self._working_directory) and (self._converter is not None and self._converter.is_booted())

    ##########################################
    ############### GETTER ###################
    ##########################################

    def _get_direct_read_file_formats(self) -> list[str]:
        """Return a list of file extensions that can be read directly without OCR."""
        return ["txt", "md"]
    
    def _get_default_path_template(self) -> str:
        return "{filename}"
    
    def get_title(self) -> str:
        return f"{self._metadata.correspondent} {self._metadata.document_type} {self._metadata.day}.{self._metadata.month}.{self._metadata.year}"

    def get_metadata(self) -> DocMetadata:
        return self._metadata
    
    def get_tags(self) -> list[str]:
        return self._tags or []
    
    def get_content(self) -> str:
        return self._content
    
    def get_date_string(self, pattern:str = "%Y-%m-%d") -> str|None:
        """Return the document creation date as a string in the given format, or None if not available."""
        if not self._metadata.year:
            return None
        month = self._metadata.month or "01"
        day = self._metadata.day or "01"
        try:
            from datetime import datetime
            dt = datetime(int(self._metadata.year), int(month), int(day))
            return dt.strftime(pattern)
        except ValueError:
            return None

    ##########################################
    ############## CONTENT ###################
    ##########################################

    async def _extract_text(self) -> str:
        if not self.is_booted():
            raise RuntimeError("DocHelper: cannot extract text, document not booted")
        # if we can read content directly, let's do it
        if self._extension in self._get_direct_read_file_formats():
            text = self._extract_text_directly()
            if text is None or not text.strip():
                raise RuntimeError(f"Error reading text directly from file '{self._converted_file}'")
            return text
        else:
            # otherwise we need to use Vision LLM OCR
            text = await self._extract_text_smart()
            if text is None or not text.strip():
                raise RuntimeError(f"Error extracting text from file '{self._converted_file}'")
            return text
        
    def _extract_text_directly(self) -> str|None:
        """Try to extract text directly from the file without OCR."""
        return self._helper_file.read_text_file(self._converted_file)
    
    async def _extract_text_smart(self) -> str|None:
        """Use Vision LLM to extract text from the file, without any pre-processing."""
        minimum_text_chars = 40
        page_texts: list[str] = []
        page_dpi = 96
        try:
            doc = fitz.open(self._converted_file)

            #iterate pages
            for page_num, page in enumerate(doc):
                direct_text = page.get_text().strip()

                #make sure there is some text on the page
                if len(direct_text) >= minimum_text_chars:
                    page_texts.append(direct_text)
                    continue

                # Page has not enough text, fall back to Vision LLM OCR if possible
                pix = page.get_pixmap(dpi=page_dpi)
                #convert the page to png and to base64 for LLM input
                png_bytes = pix.tobytes("png")
                b64 = base64.b64encode(png_bytes).decode("ascii")

                #run the vision model on the created image
                page_text = await self._call_vision_llm(b64)
                if page_text:
                    page_texts.append(page_text)
            doc.close()
        except Exception as exc:
            self.logging.error("Error extracting text from PDF '%s': %s", self._converted_file, exc)
            return None
        return "\n\n".join(page_texts)

    ##########################################
    ################# LLM ####################
    ##########################################
    async def _call_vision_llm(self, png_b64_data: str) -> str|None:
        """
        Send a base64-encoded image to the Vision LLM and return extracted text.

        Uses the Ollama-native vision format: content is a plain string, images are
        passed as a separate list of raw base64 strings (no data URI prefix).
        """
        messages = [
            {
                "role": "user",
                "content": (
                    "Transcribe all text from this image exactly as it appears. "
                    "Output plain text only — no markdown, no bullet points, no headers, "
                    "no formatting symbols. Preserve line breaks."
                ),
                "images": [png_b64_data],
            }
        ]
        try:
            return await self._llm_client.do_chat_vision(messages=messages)
        except Exception as exc:
            self.logging.error("Vision LLM call failed for Document '%s': %s", self._converted_file, exc)
            return None
        
    ##########################################
    ################# META ###################
    ##########################################
    async def _read_metadata(self) -> DocMetadata:
        """Read metadata from the file path using the configured template."""
        #read from path primary
        path_meta = self._read_meta_from_path()        
        #fill up using llm
        llm_meta = await self._read_meta_from_content()
        #merge both
        return DocMetadata(
            correspondent=path_meta.correspondent or llm_meta.correspondent,
            document_type=path_meta.document_type or llm_meta.document_type,
            year=path_meta.year or llm_meta.year,
            month=path_meta.month or llm_meta.month,
            day=path_meta.day or llm_meta.day,
            title=path_meta.title or llm_meta.title,
            filename=self._helper_file.get_basename(self._source_file, True)
        )

    def _read_meta_from_path(self) -> DocMetadata:
        known_vars = frozenset({
            "correspondent", "document_type", "year", "month", "day", "title", "filename"
        })
        positional_vars = [m.group(1) for m in re.finditer(r"\{([^}]+)\}", self._path_template)
            if m.group(1) != "filename"]
        
        try:
            rel = os.path.relpath(self._source_file, self._root_path)
        except ValueError:
            rel = os.path.basename(self._source_file)

        rel = rel.replace("\\", "/")
        segments = rel.split("/")
        filename = segments[-1]
        dir_parts = segments[:-1]

        path_meta = DocMetadata(filename=filename)
        for i, var in enumerate(positional_vars):
            if i >= len(dir_parts):
                break
            value = dir_parts[i]
            if var in known_vars and self._validate_segment_from_path_meta(var, value):
                setattr(path_meta, var, value)
        if not path_meta.correspondent:
            raise RuntimeError(f"Document: correspondent is required in path metadata but not found for file '{self._source_file}' with template '{self._path_template}'")
        return path_meta
    
    def _validate_segment_from_path_meta(self, var: str, value: str) -> bool:
        """Return True if value is a valid assignment for var."""
        numeric_validators: dict[str, re.Pattern] = {
            "year":  re.compile(r"^\d{4}$"),
            "month": re.compile(r"^\d{1,2}$"),
            "day":   re.compile(r"^\d{1,2}$"),
        }
        if var in numeric_validators:
            return bool(numeric_validators[var].match(value))
        return bool(value.strip())
    
    async def _read_meta_from_content(self) -> DocMetadata:
        """Ask the LLM to extract metadata and parse the JSON response."""
        #read the existing data from dms cache
        prompt = self._get_prompt_extraction() + (self._get_prompt_cache() or "") + "\nDocument text:\n" + self._content[:3000]
        messages = [{"role": "user", "content": prompt}]
        try:
            # run the prompt
            raw = await self._llm_client.do_chat(messages)
            #parse the respone json
            raw = re.sub(r"```(?:json)?\s*", "", raw).strip().rstrip("`")
            data = json.loads(raw)
            return DocMetadata(
                correspondent=data.get("correspondent") or None,
                document_type=data.get("document_type") or None,
                year=data.get("year") or None,
                month=data.get("month") or None,
                day=data.get("day") or None,
                title=data.get("title") or None,
                filename=self._helper_file.get_basename(self._source_file, True))
        except Exception as exc:
            raise RuntimeError(f"Failed to read meta from content using llm '{self._source_file}': {exc}")        

    ##########################################
    ################# TAGS ###################
    ##########################################

    async def _read_tags_from_content(self) -> list[str]:
        prompt = self._get_prompt_tags() + self._content[:3000]
        messages = [{"role": "user", "content": prompt}]
        try:
            raw = await self._llm_client.do_chat(messages)
            raw = re.sub(r"```(?:json)?\s*", "", raw).strip().rstrip("`")
            data = json.loads(raw)
            if isinstance(data, list):
                return [str(t) for t in data if t]
            raise ValueError(f"Tag extraction LLM response is not a list for file '{self._source_file}': {raw}")
        except Exception as exc:
            raise RuntimeError(f"Failed to read tags from content using llm '{self._source_file}': {exc}") 

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
        return result
    
    ##########################################
    ############### PROMPTS ##################
    ##########################################

    def _get_prompt_extraction(self) -> str:
        """Public method to expose the metadata extraction prompt for testing purposes."""
        return ("""
            You are a document metadata extractor. Analyse the following document text and extract metadata.

            LANGUAGE: All extracted text values must be in %s.

            Return a JSON object with these fields (use null if unknown):
            {
                "document_type": "type of document (e.g. Rechnung, Vertrag, Brief, Quittung)",
                "title": "short document title",
                "year": "4-digit year of document creation date, if detectable. Aka YYYY",
                "month": "2-digit month of document creation date, if detectable. Aka MM",
                "day": "2-digit day of document creation date, if detectable. Aka DD"
            }

            Return ONLY the JSON object, no other text.
            """ % self._language).strip()
    
    def _get_prompt_cache(self) -> str|None:
        """Public method to expose DMS cache for testing purposes."""
        cache = self._get_dms_cache()
        if not cache or not "document_types" in cache:
            return None
        cache_line = "Document types: %s" % ", ".join(cache["document_types"])
        return ("""
            EXISTING VALUES IN THE SYSTEM (use these exact names if they match):
            %s
            Only invent a new name if absolutely no existing value fits.
            """ % cache_line).strip()
    
    def _get_prompt_tags(self) -> str|None:
        """Public method to expose the tag extraction prompt for testing purposes."""        
        cache = self._get_dms_cache()        
        tag_names = cache.get("tags", [])
        tag_context = ", ".join(tag_names) if tag_names else "(none)"
        return ("""
            You are a document tagger. Select the most relevant tags for the document below.

            LANGUAGE: All tag names must be in %s.

            WHAT A TAG IS:
            - A broad document category: Rechnung, Gutschrift, Versicherung, Vertrag, Lohnzettel, Kündigung
            - A time period: 2026, Q1 2026
            - A business domain: Buchhaltung, Personal, Steuern, Marketing

            WHAT A TAG IS NOT — never use these as tags:
            - The correspondent or sender name (already stored in the correspondent field)
            - Specific amounts, prices, tax rates, or percentages (e.g. "German VAT 19%%", "107.46 Euro")
            - Bank details, IBANs, or technical reference numbers
            - Overly generic words like "Company", "Document", "Payment", "Contact Information"

            RULES:
            1. PREFER existing tags — use exact names from the list if they fit.
            2. Only propose a NEW tag if the document category is genuinely not covered by any existing tag.
            3. Return at most 3 tags total.
            4. Return [] if no tag applies.

            EXISTING TAGS (prefer these exact names):
            %s

            Return ONLY a JSON array of tag name strings, e.g. ["Rechnung", "2026"].

            Document text:
            """ % (self._language, tag_context)).strip()