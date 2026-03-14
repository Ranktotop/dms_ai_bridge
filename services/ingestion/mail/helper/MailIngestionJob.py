from services.ingestion.mail.helper.MailParser import ParsedMail
from services.ingestion.mail.helper.MailIngestionJobItem import MailIngestionJobItem
from shared.helper.HelperFile import HelperFile
from shared.helper.HelperConfig import HelperConfig
from shared.clients.cache.CacheClientInterface import CacheClientInterface
from shared.clients.dms.DMSClientInterface import DMSClientInterface
from shared.clients.llm.LLMClientInterface import LLMClientInterface
from shared.clients.ocr.OCRClientInterface import OCRClientInterface
from shared.clients.prompt.PromptClientInterface import PromptClientInterface
import os
class MailIngestionJob:
    def __init__(self, 
                 owner_id:int,
                 helper_config: HelperConfig,
                 dms_client:DMSClientInterface, 
                 llm_client:LLMClientInterface, 
                 cache_client:CacheClientInterface, 
                 ocr_client:OCRClientInterface, 
                 prompt_client:PromptClientInterface,
                 job_id:str, 
                 parsed_mail:ParsedMail, 
                 working_dir:str, 
                 inbox_message_id:str, 
                 inbox_path:str = None, 
                 inbox_tags:list[str] = None, 
                 inbox_doc_type:str = None):
        self._owner_id = owner_id
        self._working_dir = working_dir
        self._job_id = job_id
        self._parsed_mail = parsed_mail
        self._helper_file = HelperFile()
        self._items:list[MailIngestionJobItem] = []
        self._inbox_message_id = inbox_message_id
        self._inbox_path = inbox_path
        self._inbox_tags = inbox_tags
        self._inbox_doc_type = inbox_doc_type
        self._dms_client = dms_client
        self._llm_client = llm_client
        self._cache_client = cache_client
        self._ocr_client = ocr_client
        self._prompt_client = prompt_client
        self._helper_config = helper_config
        self.logging = helper_config.get_logger()

    #############################################
    ############# ITEM MANAGEMENT ###############
    #############################################

    def get_item_count(self) -> int:
        """
        Returns the number of items in this job.

        Returns:
            int: The number of items in this job.
        """
        return len(self._items)

    async def _create_items(self):
        """
        Create MailIngestionJobItems for the mail body and each attachment, writing temp files as needed.
        If an item is already cached, it is not added to the items list for processing.

        Raises:
            Exception: If working directory cannot be created or files cannot be written.
        """
        if not self._parsed_mail:
            return
        if not self._helper_file.create_folder(self._working_dir):
            raise Exception("Failed to create working directory for mail ingestion job: %s" % self._working_dir)
        
        # create the body item if we have body content
        if self._parsed_mail.mail_content is not None:
            item_index = len(self._items) +1
            # decide if we use markdown or plain text
            mail_content = self._parsed_mail.mail_content
            using_markdown = mail_content.is_valid_markdown()
            ingestion_content = mail_content.markdown_content if using_markdown else mail_content.content
            file_extension = ".md" if using_markdown else ".txt"

            # create a temp body file
            file_path = os.path.join(self._working_dir, "mail_body_%d%s" % (item_index, file_extension))
            if not self._helper_file.write_text_file(ingestion_content, file_path):
                raise Exception("Failed to write mail body content to file: %s" % file_path)

            self._items.append(
                MailIngestionJobItem(
                    owner_id = self._owner_id,
                    dms_client=self._dms_client,
                    llm_client=self._llm_client,
                    cache_client=self._cache_client,
                    ocr_client=self._ocr_client,
                    prompt_client=self._prompt_client,
                    helper_config=self._helper_config,
                    working_dir = os.path.join(self._working_dir, str(item_index)),
                    job_id=item_index, 
                    file_path=file_path,
                    is_attachment=False,
                    sender_name=self._parsed_mail.sender_name,
                    sender_mail=self._parsed_mail.sender_mail,
                    subject=self._parsed_mail.subject,
                    year=self._parsed_mail.year,
                    month=self._parsed_mail.month,
                    day=self._parsed_mail.day,
                    hour=self._parsed_mail.hour,
                    minute=self._parsed_mail.minute,
                    second=self._parsed_mail.second,
                    content=ingestion_content,
                    inbox_message_id=self._inbox_message_id,
                    inbox_path=self._inbox_path,
                    inbox_tags=self._inbox_tags,
                    inbox_doc_type=self._inbox_doc_type))
            
        # create attachments
        if self._parsed_mail.attachments:
            for attachment in self._parsed_mail.attachments:
                item_index = len(self._items) +1
                file_path = os.path.join(self._working_dir, "attachment_%d_%s" % (item_index, attachment.filename))                
                if not self._helper_file.write_file_bytes(attachment.file_bytes, file_path):
                    raise Exception("Failed to write mail attachment content to file: %s" % file_path)
                
                self._items.append(
                    MailIngestionJobItem(
                        owner_id = self._owner_id,
                        dms_client=self._dms_client,
                        llm_client=self._llm_client,
                        cache_client=self._cache_client,
                        ocr_client=self._ocr_client,
                        prompt_client=self._prompt_client,
                        helper_config=self._helper_config,
                        working_dir = os.path.join(self._working_dir, str(item_index)),
                        job_id=item_index, 
                        file_path=file_path,
                        is_attachment=True,
                        sender_name=self._parsed_mail.sender_name,
                        sender_mail=self._parsed_mail.sender_mail,
                        subject=self._parsed_mail.subject,
                        year=self._parsed_mail.year,
                        month=self._parsed_mail.month,
                        day=self._parsed_mail.day,
                        hour=self._parsed_mail.hour,
                        minute=self._parsed_mail.minute,
                        second=self._parsed_mail.second,
                        content="",
                        inbox_message_id=self._inbox_message_id,
                        inbox_path=self._inbox_path,
                        inbox_tags=self._inbox_tags,
                        inbox_doc_type=self._inbox_doc_type))   
                
        # now remove items which are already cached
        items = []
        for item in self._items:
            if await item._is_cached():
                self.logging.info("Item %d in job %d is already cached. Skipping processing for this item.", item.get_job_id(), self._job_id)
                item.delete()
            else:
                items.append(item)
        self._items = items
    
    #############################################
    ################# PROCESS ###################
    #############################################

    async def boot(self, batch_index:int, overall_batches:int, overall_items:int) -> bool:
        """
        Boots each item in the Jobs item list

        Args:
            batch_index (int): The index of the current batch being processed.
            overall_batches (int): The total number of batches being processed.
            overall_items (int): The total number of items across all batches.

        Returns:
            bool: True if there are any items left to process after booting
        """
        # create the items        
        await self._create_items()
        if not self._items:
            return False

        # now boot each item. If an item failed, it deletes itself and gets removed from the item list
        booted = []
        for item in self._items:
            if item.boot(
                batch_index=batch_index, 
                overall_batches=overall_batches, 
                overall_items=overall_items,
                parent_job_index=self._job_id):
                booted.append(item)
        self._items = booted
        return len(self._items) > 0

    async def upload(self, batch_index:int, overall_batches:int, overall_items:int)->bool:
        """
        Uploads each item to its DMS System. 
        If an item already exists, it writes itself to cache 
        In any case of error the item deletes itself

        Args:
            batch_index (int): The index of the current batch being processed.
            overall_batches (int): The total number of batches being processed.
            overall_items (int): The total number of items across all batches.

        Returns:
            bool: True if there are any items left to process after booting
        """
        if not self._items:
            return False
        # now upload each item. If an item failed, it deletes itself and gets removed from the item list
        uploaded = []
        for item in self._items:
            if item.upload(
                batch_index=batch_index, 
                overall_batches=overall_batches, 
                overall_items=overall_items,
                parent_job_index=self._job_id):
                uploaded.append(item)
        self._items = uploaded
        return len(self._items) > 0
    
    async def load_content(self, batch_index:int, overall_batches:int, overall_items:int)->bool:
        """
        Loads content for each item in the job.

        Args:
            batch_index (int): The index of the current batch being processed.
            overall_batches (int): The total number of batches being processed.
            overall_items (int): The total number of items across all batches.

        Returns:
            bool: True if there are any items left to process after booting
        """
        if not self._items:
            return False
        # now load content for each item. If an item failed, it deletes itself and gets removed from the item list
        loaded = []
        for item in self._items:
            if item.load_content(
                batch_index=batch_index, 
                overall_batches=overall_batches, 
                overall_items=overall_items,
                parent_job_index=self._job_id):
                loaded.append(item)
        self._items = loaded
        return len(self._items) > 0
    
    async def format_content(self, batch_index:int, overall_batches:int, overall_items:int)->bool:
        """
        Formats content for each item in the job.

        Args:
            batch_index (int): The index of the current batch being processed.
            overall_batches (int): The total number of batches being processed.
            overall_items (int): The total number of items across all batches.

        Returns:
            bool: True if there are any items left to process after booting
        """
        if not self._items:
            return False
        # now format content for each item. If an item failed, it deletes itself and gets removed from the item list
        formatted = []
        for item in self._items:
            if item.format_content(
                batch_index=batch_index, 
                overall_batches=overall_batches, 
                overall_items=overall_items,
                parent_job_index=self._job_id):
                formatted.append(item)
        self._items = formatted
        return len(self._items) > 0