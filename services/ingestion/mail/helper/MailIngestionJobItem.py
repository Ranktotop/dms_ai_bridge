from services.ingestion.entities.DocumentMail import DocumentMail
from shared.helper.HelperFile import HelperFile
from shared.helper.HelperConfig import HelperConfig
from shared.clients.cache.CacheClientInterface import CacheClientInterface
from shared.clients.dms.DMSClientInterface import DMSClientInterface
from shared.clients.llm.LLMClientInterface import LLMClientInterface
from shared.clients.ocr.OCRClientInterface import OCRClientInterface
from shared.clients.prompt.PromptClientInterface import PromptClientInterface
from services.ingestion.exceptions import DocumentValidationError
import re
import hashlib
class MailIngestionJobItem:
    def __init__(self, 
                 owner_id:int,
                 dms_client:DMSClientInterface, 
                 llm_client:LLMClientInterface, 
                 cache_client:CacheClientInterface, 
                 ocr_client:OCRClientInterface, 
                 prompt_client:PromptClientInterface,
                 helper_config:HelperConfig,
                 working_dir:str,
                 job_id:str, 
                 file_path:str, 
                 is_attachment:bool, 
                 sender_name:str, 
                 sender_mail:str, 
                 subject:str,
                 year:int, 
                 month:int, 
                 day:int, 
                 hour:int, 
                 minute:int, 
                 second:int,
                 content:str,
                 inbox_message_id:str,
                 inbox_path:str,
                 inbox_tags:list[str] = None,
                 inbox_doc_type:str = None):
        self._owner_id = owner_id
        self._job_id = job_id
        self._file_path = file_path
        self._is_attachment = is_attachment
        self._sender_name = sender_name
        self._sender_mail = sender_mail
        self._dms_client = dms_client
        self._llm_client = llm_client
        self._cache_client = cache_client
        self._ocr_client = ocr_client
        self._prompt_client = prompt_client
        self._subject = subject
        self._year = year
        self._month = month
        self._day = day
        self._hour = hour
        self._minute = minute
        self._second = second
        self._content = content
        self._custom_fieldname_email = "Sender Email"
        self._inbox_message_id = inbox_message_id
        self._inbox_path = inbox_path
        self._inbox_tags = inbox_tags
        self._inbox_doc_type = inbox_doc_type
        self._working_dir = working_dir
        self._document_mail:DocumentMail|None = None
        self._helper_file = HelperFile()
        self._helper_config = helper_config
        self.logging = helper_config.get_logger()
        self._is_booted = False
        self._is_uploaded = False
        self._dms_doc_id = None
        # generate file hash for caching purposes
        self._file_bytes = None
        with open(file_path, "rb") as f:
            self._file_bytes = f.read()
        self._file_hash = hashlib.sha256(self._file_bytes).hexdigest()

    #############################################
    ################ CHECKERS ###################
    #############################################

    async def _is_cached(self) -> bool:
        """
        Checks if this mail item has already been ingested by looking up its cache key.

        Returns:
            bool: True if the item is found in the cache, False otherwise.
        """
        cached_doc_id = await self._cache_client.do_get(self.get_cache_key())
        if cached_doc_id is not None:
            return True
        return False

    #############################################
    ################ GETTERS ####################
    #############################################

    def get_job_id(self) -> str:
        """
        Returns the ID of the job this item belongs to.

        Returns:
            str: The job ID.
        """
        return self._job_id

    def get_content_prefix(self)-> str:
        """
        Return the content prefix for this mail item, which gets injected into the content (not file!)

        Returns:
            str: The content prefix string in the format "[E-Mail | Von: sender | Betreff: subject | Datum: YYYY.MM.DD]".
        """
        return "[E-Mail | Von: %s | Betreff: %s | Datum: %s.%s.%s]" % (
            self.get_sender(include_mail=True, include_name=True), self.get_subject(), self.get_year(), self.get_month(), self.get_day()
        )
    
    def get_year(self) -> str:
        """
        Return the year as a zero-padded string, or an empty string if not available.

        Returns:
            str: The year in "YYYY" format, or "" if the year is not available.
        """
        return "%04d" % self._year if self._year else ""

    def get_month(self) -> str:
        """
        Return the month as a zero-padded string, or an empty string if not available.

        Returns:
            str: The month in "MM" format, or "" if the month is not available.
        """
        return "%02d" % self._month if self._month else ""
    
    def get_day(self) -> str:
        """
        Return the day of the month as a zero-padded string, or an empty string if not available.

        Returns:
            str: The day of the month in "DD" format, or "" if the day
        """
        return "%02d" % self._day if self._day else ""
    
    def get_sender(self, include_mail:bool = True, include_name:bool = True) -> str:
        """
        Formats a sender display string combining name and email address.

        Args:
            include_mail: Whether to include the email address in the output.
            include_name: Whether to include the sender's name in the output.

        Returns:
            str: Formatted sender string based on the specified options. Examples:
        """
        if include_name and include_mail and self._sender_name and self._sender_mail:
            return "%s <%s>" % (self._sender_name, self._sender_mail)
        elif include_name and self._sender_name:
            return self._sender_name
        elif include_mail and self._sender_mail:
            return self._sender_mail
        else:
            return ""
    
    def get_subject(self) -> str:
        """
        Strip common reply/forward prefixes from an email subject line.

        Removes RE:/FW:/AW:/WG: prefixes (case-insensitive, applied repeatedly)
        so the resulting string is suitable as a title prefix without noise.

        Returns:
            Subject with all leading reply/forward prefixes removed.
        """
        # apply repeatedly because subjects can be nested: "Re: Fw: Re: Invoice"
        cleaned = self._subject
        while True:
            stripped = re.sub(r"^(RE|FW|AW|WG)\s*:\s*", "", cleaned, flags=re.IGNORECASE).strip()
            if stripped == cleaned:
                break
            cleaned = stripped
        return cleaned
    
    def get_cache_key(self) -> str:
        """
        Generate a cache key for this email item based on sender, subject, and date.

        Combines sender email, cleaned subject, and timestamp into a single string,
        then applies a hash function to produce a fixed-length cache key.

        Returns:
            A string representing the cache key for this email item.
        """        
        base = "ingestion:mail"
        sender_name = self.get_sender()
        if not sender_name:
            raise Exception("Cannot generate cache key for mail item without sender information")
        sender_mail_hash = hashlib.sha256(sender_name.encode("utf-8")).hexdigest()
        detail_key = self._dms_engine_name+":" + sender_mail_hash + ":" + self._file_hash
        return "%s:%s" % (base, detail_key)
    
    #############################################
    ################# PROCESS ###################
    #############################################

    def delete(self) -> bool:
        """
        Deletes the working directory and all its contents for this mail item. 
        This should be called when the item is cached and does not need to be processed.
        
        Returns:
            bool: True if deletion was successful or if the folder does not exist, False if deletion failed.
        """
        success = True
        if self._helper_file.folder_exists(self._working_dir):
            if not self._helper_file.remove_folder(self._working_dir):
                self.logging.error("Failed to delete working directory for mail ingestion job item %d: %s", self._job_id, self._working_dir)
                success = False
        
        # reset the important vars
        self._is_booted = False
        self._is_uploaded = False
        self._dms_doc_id = None
        return success

    def boot(self, batch_index:int, overall_batches:int, overall_items:int, parent_job_index:int) -> bool:
        """
        Boots this job item for processing. This is where you would implement any logic needed to prepare the item for ingestion,
        such as uploading the file to a temporary storage location, extracting metadata, or performing any necessary transformations.

        Args:
            batch_index (int): The index of the current batch being processed.
            overall_batches (int): The total number of batches being processed.
            overall_items (int): The total number of items across all batches.
            parent_job_index (int): The index of the parent job this item belongs to, used for logging and tracking purposes.

        Returns:
            bool: True if the item was successfully booted and is ready for processing, False if not.
        """
        # create working dir
        if not self._helper_file.create_folder(self._working_dir):
            self.logging.error("Failed to create working directory for mail ingestion job item %d: %s", self._job_id, self._working_dir)
            self.delete()
            return False
        
        # decide if forced or fallback
        forced_correspondent = None
        forced_document_type = None
        forced_year = None
        forced_month = None
        forced_day = None
        forced_title = None
        fallback_correspondent = None
        fallback_title = None
        fallback_year = None
        fallback_month = None
        fallback_day = None
        if self._is_attachment:
            fallback_correspondent = self.get_sender(include_mail=False, include_name=True)
            fallback_title = self.get_subject() if self.get_subject() else None
            fallback_year = self.get_year() if self.get_year() else None
            fallback_month = self.get_month() if self.get_month() else None
            fallback_day = self.get_day() if self.get_day() else None
        else:
            forced_correspondent = self.get_sender(include_mail=False, include_name=True)
            forced_title = self.get_subject() if self.get_subject() else None
            forced_year = self.get_year() if self.get_year() else None
            forced_month = self.get_month() if self.get_month() else None
            forced_day = self.get_day() if self.get_day() else None
            
        # init the document        
        self._document_mail = DocumentMail(
            source_file=self._file_path,
            working_directory=self._working_dir,
            helper_config=self._helper_config,
            llm_client=self._llm_client,
            dms_client=self._dms_client,
            file_bytes=self._file_bytes,
            file_hash=self._file_hash,
            ocr_client=self._ocr_client,
            prompt_client=self._prompt_client,
            forced_tags=self._inbox_tags,
            forced_document_type=forced_document_type,
            forced_title=forced_title,
            forced_correspondent=forced_correspondent,
            forced_year=forced_year,
            forced_month=forced_month,
            forced_day=forced_day,
            fallback_title=fallback_title,
            fallback_correspondent=fallback_correspondent,
            fallback_year=fallback_year,
            fallback_month=fallback_month,
            fallback_day=fallback_day)
        
        overall_index = parent_job_index + self._job_id
        prefix = "[%s/%s], batch [%s/%s] " % (overall_index, overall_items, batch_index, overall_batches)        
        try:
            self._document_mail.boot()
            self.logging.info("%sBooted '%s'.", prefix, self._file_path)
            self._is_booted = True
            return True
        except DocumentValidationError as e:
            self.logging.error("%sSkipping '%s': %s", prefix, self._file_path, e, color="red")
        except Exception as e:
            self.logging.error("%sFailed to boot document '%s': %s", prefix, self._file_path, e)
        # on error    
        self.delete()
        return False
    
    async def upload(self, batch_index:int, overall_batches:int, overall_items:int, parent_job_index:int) -> bool:
        """
        Uploads the document to its dms-system.
        If the file already exists, this returns False after adding to cache.
        On other errors, it returns False after logging and deleting the item.
        In any error case the file deletes itself.

        Args:
            batch_index (int): The index of the current batch being processed.
            overall_batches (int): The total number of batches being processed.
            overall_items (int): The total number of items across all batches.
            parent_job_index (int): The index of the parent job this item belongs to, used for logging and tracking purposes.

        Returns:
            bool: True if the upload was successful, False if not.        
        """
        if not self._is_booted:            
            self.logging.error("Cannot upload mail item %d in job %d because it is not booted.", self._job_id, parent_job_index)
            self.delete()
            return False

        # Upload original file to dms
        file_name = self._document_mail.get_source_file(filename_only=True)
        file_path = self._document_mail.get_source_file()
        file_bytes = self._document_mail.get_file_bytes()
        overall_index = parent_job_index + self._job_id
        prefix = "[%s/%s], batch [%s/%s] " % (overall_index, overall_items, batch_index, overall_batches)        

        try:
            self.logging.info("%sUploading '%s' to %s-DMS.", prefix, file_path, self._dms_client.get_engine_name())
            self._dms_doc_id = await self._dms_client.do_upload_document(
                file_bytes=file_bytes,
                file_name=file_name,
                owner_id=self._owner_id,
            )
            if not self._dms_doc_id:
                self.logging.error("%sUpload failed for '%s': No document ID returned by DMS.", prefix, file_path)
            else:
                #save to cache on success
                await self._cache_client.do_set(self.get_cache_key(), str(self._dms_doc_id))
                self._is_uploaded = True
                return True
        # if we get a file exists error, the file has already been uploaded. Add it to the cache and return False
        except FileExistsError as e:
            self._dms_doc_id: int | None = e.args[0] if e.args else None
            if self._dms_doc_id is not None:
                self.logging.warning("%sSkipping '%s': duplicate of DMS doc id=%d. Caching hash.",prefix, file_path, self._dms_doc_id, color="yellow")                
                await self._cache_client.do_set(self.get_cache_key(), str(self._dms_doc_id))
            else:
                self.logging.warning("%sSkipping '%s': already exists in DMS.", prefix, file_path)  
        except Exception as e:
            self.logging.error("%sUpload failed for '%s': %s", prefix, file_path, e)      

        self.delete()
        return False


