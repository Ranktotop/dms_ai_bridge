
from shared.helper.HelperConfig import HelperConfig
from services.ingestion.mail.helper.MailAccountConfigHelper import MailAccountConfig, MailFolderConfig
from services.ingestion.mail.helper.MailParser import MailParser, ParsedMail
from services.ingestion.mail.helper.MailIngestionJob import MailIngestionJob
from shared.clients.cache.CacheClientInterface import CacheClientInterface
from shared.clients.dms.DMSClientInterface import DMSClientInterface
from shared.clients.llm.LLMClientInterface import LLMClientInterface
from shared.clients.ocr.OCRClientInterface import OCRClientInterface
from shared.clients.prompt.PromptClientInterface import PromptClientInterface
import os
import email as _email

class MailIngestionJobManager:
    def __init__(self, helper_config: HelperConfig, working_dir:str, dms_client:DMSClientInterface, llm_client:LLMClientInterface, cache_client:CacheClientInterface, ocr_client:OCRClientInterface, prompt_client:PromptClientInterface):
        self._working_dir = working_dir
        self._config = helper_config
        self._logging = helper_config.get_logger()
        self._parser = MailParser(helper_config=helper_config)
        self._dms_client = dms_client
        self._llm_client = llm_client
        self._cache_client = cache_client
        self._ocr_client = ocr_client
        self._prompt_client = prompt_client
        self._jobs:list[MailIngestionJob] = []   # in-memory store of ingestion jobs; replace with DB in production

    #############################################
    ############## JOB MANAGEMENT ###############
    #############################################

    def add_message(
        self,
        inbox_message_id: str,
        raw_bytes: bytes,
        account: MailAccountConfig,
        folder: MailFolderConfig
    ) -> None:
        """
        Processes the given raw email bytes and creates a MailIngestionJob from it

        Args:
            inbox_message_id (str): The unique identifier of the email message in the inbox.
            raw_bytes (bytes): The raw email content as bytes.
            account (MailAccountConfig): The mail account configuration containing recipient mapping and default owner ID.
            folder (MailFolderConfig): The mail folder configuration containing path, tags and document type for the ingestion job.
        """
        # resolve owner_id by scanning To/Cc/Delivered-To/X-Original-To headers
        owner_id = self._resolve_owner_id(raw_bytes=raw_bytes, account=account)
        
        # create the ParsedMail which includes meta, body and attachments
        parsed_mail:ParsedMail = self._parser.parse(
            raw_bytes=raw_bytes,
            owner_id=owner_id,
            attachment_extensions=account.attachment_extensions,
            ingest_body=account.ingest_body,
        )

        # define job index for logging purposes
        job_index = len(self._jobs) +1
        self._jobs.append(MailIngestionJob(
            owner_id=owner_id,
            dms_client=self._dms_client,
            llm_client=self._llm_client,
            cache_client=self._cache_client,
            ocr_client=self._ocr_client,
            prompt_client=self._prompt_client,
            job_id=job_index, 
            parsed_mail=parsed_mail,
            working_dir=os.path.join(self._working_dir, str(job_index)),
            inbox_message_id=inbox_message_id,
            inbox_path=folder.path,
            inbox_tags=folder.tags,
            inbox_doc_type=folder.document_type))
    
    #############################################
    ################# PROCESS ###################
    #############################################

    async def run_jobs(self, batch_size:int = 0) -> None:
        # count overall items
        overall_items = sum(job.get_item_count() for job in self._jobs)
        
        # Create the batch list to process
        if batch_size > 0:
            job_batches = (
                [self._jobs[i:i + batch_size] for i in range(0, len(self._jobs), batch_size)]
                if batch_size > 0
                else [self._jobs]
            )
        else:
            job_batches = [self._jobs]

        # iterate batches
        for batch_index, job_batch in enumerate(job_batches):

            # boot the jobs
            booted_jobs:list[MailIngestionJob] = []
            for job in job_batch:
                if await job.boot(
                    batch_index=batch_index + 1, 
                    overall_batches=len(job_batches), 
                    overall_items=overall_items):
                    booted_jobs.append(job)

            # upload the jobs
            uploaded_jobs:list[MailIngestionJob] = []
            for job in booted_jobs:
                if await job.upload(
                    batch_index=batch_index + 1, 
                    overall_batches=len(job_batches), 
                    overall_items=overall_items):
                    uploaded_jobs.append(job)

            # load content for jobs
            content_jobs:list[MailIngestionJob] = []
            for job in uploaded_jobs:
                if await job.load_content(
                    batch_index=batch_index + 1, 
                    overall_batches=len(job_batches), 
                    overall_items=overall_items):
                    content_jobs.append(job)

            # format content for jobs
            formatted_jobs:list[MailIngestionJob] = []
            for job in content_jobs:
                if await job.format_content(
                    batch_index=batch_index + 1, 
                    overall_batches=len(job_batches), 
                    overall_items=overall_items):
                    formatted_jobs.append(job)

            # load meta for jobs
            meta_jobs:list[MailIngestionJob] = []
            for job in formatted_jobs:
                if await job.load_meta(
                    batch_index=batch_index + 1, 
                    overall_batches=len(job_batches), 
                    overall_items=overall_items):
                    meta_jobs.append(job)

            # load tags for jobs
            tag_jobs:list[MailIngestionJob] = []
            for job in meta_jobs:
                if await job.load_tags(
                    batch_index=batch_index + 1, 
                    overall_batches=len(job_batches), 
                    overall_items=overall_items):
                    tag_jobs.append(job)

            # update the jobs in dms
            updated_jobs:list[MailIngestionJob] = []
            for job in tag_jobs:
                if await job.update(
                    batch_index=batch_index + 1, 
                    overall_batches=len(job_batches), 
                    overall_items=overall_items):
                    updated_jobs.append(job)
    
    #############################################
    ################# HELPERS ###################
    #############################################

    def _resolve_owner_id(self, raw_bytes: bytes, account: MailAccountConfig) -> int:
        """
        Reads the email header and extracts informations about the recipient to determine the owner_id for this email
        
        Args:
            raw_bytes (bytes): The raw email content as bytes.
            account (MailAccountConfig): The mail account configuration containing recipient mapping and default owner ID.

        Returns:
            int: The resolved owner_id based on the email headers and account configuration.

        Raises:
            Exception: If no recipient match is found in the email headers and the account has no default owner ID set.
        """
        msg = _email.message_from_bytes(raw_bytes)
        for header in ("To", "Cc", "Delivered-To", "X-Original-To"):
            raw_val = msg.get(header, "")
            if not raw_val:
                continue
            for addr in raw_val.split(","):
                addr = addr.strip()
                # extract bare address from "Display Name <addr@example.com>" format
                if "<" in addr and ">" in addr:
                    addr = addr[addr.index("<") + 1:addr.index(">")]
                addr = addr.strip().lower()
                if addr in account.recipient_mapping:
                    return account.recipient_mapping[addr]

        # no match in any recipient header — use the account-level default
        if not account.default_owner_id:
            raise Exception("No recipient match found in email and account has no default_owner_id set")
        return account.default_owner_id