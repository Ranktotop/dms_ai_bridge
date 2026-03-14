"""Orchestrator for the mail ingestion pipeline."""
import email as _email
import os
import re
import tempfile
from dataclasses import dataclass, field

from shared.clients.cache.CacheClientInterface import CacheClientInterface
from shared.clients.dms.DMSClientInterface import DMSClientInterface
from shared.clients.dms.models.DocumentUpdate import DocumentUpdateRequest
from shared.clients.llm.LLMClientInterface import LLMClientInterface
from shared.clients.ocr.OCRClientInterface import OCRClientInterface
from shared.clients.prompt.PromptClientInterface import PromptClientInterface
from shared.helper.HelperConfig import HelperConfig
from shared.helper.HelperFile import HelperFile
from services.ingestion.entities.DocumentMail import DocumentMail
from services.ingestion.mail.helper.MailAccountConfigHelper import MailAccountConfig, MailFolderConfig
from services.ingestion.mail.helper.MailIngestionJobManager import MailIngestionJobManager
from services.ingestion.mail.helper.MailFetcher import MailFetcher
from services.ingestion.mail.helper.MailParser import ParsedMail, MailAttachmentInput


class MailIngestionService:
    """Orchestrates the mail ingestion pipeline for a single DMS engine.

    Unlike the per-document sequential approach, all documents from an entire
    folder run are processed in phase-batches so the LLM loads each model only
    once per phase:

      Phase 0 — boot:           convert file, read file bytes
      Phase 1 — upload:         DMS upload (early gate before expensive LLM work)
      Phase 2 — load_content:   Vision LLM stays loaded for the full batch
      Phase 3 — format_content: Chat LLM formats every document in sequence
      Phase 4 — load_metadata:  Chat LLM extracts metadata; additional_* hints flow forward
      Phase 5 — load_tags:      Chat LLM extracts tags; additional_* hints flow forward
      Phase 6 — update DMS:     patch every document; mark successful messages in cache

    Rollback: if any phase fails for a document, its message_id is added to
    failed_messages.  Every subsequent phase skips and rolls back documents whose
    message_id is in that set, so a partial failure (e.g. an attachment error)
    automatically unwinds already-uploaded siblings from the same message.
    """

    ##########################################
    ############# LIFECYCLE ##################
    ##########################################

    def __init__(
        self,
        helper_config: HelperConfig,
        dms_client: DMSClientInterface,
        llm_client: LLMClientInterface,
        cache_client: CacheClientInterface,
        ocr_client: OCRClientInterface | None = None,
        prompt_client: PromptClientInterface | None = None,
    ) -> None:
        self._config = helper_config
        self.logging = helper_config.get_logger()
        self._dms_client = dms_client
        self._llm_client = llm_client
        self._cache_client = cache_client
        self._ocr_client = ocr_client
        self._prompt_client = prompt_client

        self._helper_file = HelperFile()
        self._fetcher = MailFetcher(helper_config=helper_config)
        self._jobmanager = MailIngestionJobManager(
            helper_config=helper_config, 
            working_dir=self._helper_file.generate_tempfolder(path_only=True),
            dms_client=dms_client,
            llm_client=llm_client,
            cache_client=cache_client,
            ocr_client=ocr_client,
            prompt_client=prompt_client
        )

    ##########################################
    ############# INGESTION ##################
    ##########################################

    async def do_ingest_account_folder(
        self,
        account: MailAccountConfig,
        folder: MailFolderConfig,
        batch_size: int = 0,
    ) -> None:
        """Ingest all unprocessed messages from one account/folder combination.

        Fetches messages, parses them all into _MailDocSpec entries, then hands
        the full list to _ingest_batch for phase-based processing.  The message
        is marked processed in cache only after all its parts succeed.

        Args:
            account: Full mail account config including IMAP credentials.
            folder: The specific IMAP folder to process.
            batch_size: Maximum number of messages to process per run.
                0 means no limit (all unprocessed messages are processed).
        """
        self.logging.info("Processing folder '%s' on account '%s'.", folder.path, account.id)

        # fetch all mails by using mail fetcher
        messages = self._fetcher.fetch_all_mails(account=account, folder=folder)        
        if not messages:
            return
        self.logging.info("Found %d message(s) in folder '%s' on account '%s'.",len(messages), folder.path, account.id,)

        # iterate messages and create MailIngestionJobs. 
        for message_id, raw_bytes in messages.items():
            self._jobmanager.add_message(
                inbox_message_id=message_id,
                raw_bytes=raw_bytes,
                account=account,
                folder=folder
            )
        
        # now run the process
        self._jobmanager.run_jobs(batch_size=batch_size)
    
    async def _ingest_batch(
        self,
        message_doc_map: dict[str, list[int]],
        total_unprocessed: int = 0,
    ) -> None:
        """Process all _MailDocSpec entries through the ingestion phases in batch.

        Each phase processes every surviving document in sequence.  When a document
        fails, its message_id is added to failed_messages and every sibling document
        from the same message is rolled back and cleaned up in subsequent phases.

        Temp files written by _parse_messages are always deleted in a finally block
        — doc.cleanup() only removes the working directory, not the source_file.

        Args:
            all_specs: Flat list of all document specs for this folder run.
            message_doc_map: message_id → indices into all_specs (for cache marking).
            total_unprocessed: Total number of unprocessed messages in the folder
                (including those not in this batch due to batch_size). Used as the
                denominator in progress prefixes so the counter reflects the full
                backlog rather than just the current batch.
        """
        # use the full backlog count as the denominator when known — a message with body only
        # produces one spec, so message count is a close enough proxy for spec count;
        # fall back to len(all_specs) when total_unprocessed was not provided
        overall_count = total_unprocessed if total_unprocessed > 0 else len(all_specs)
        # messages whose pipeline has already failed — used to cascade rollback across phases
        failed_messages: set[str] = set()
        # messages that failed exclusively because all their parts were already in the DMS;
        # these are treated as successfully processed (not retried) because the content is already there
        dup_only_messages: set[str] = set()

        try:
            # ── Phase 0: boot ──────────────────────────────────────────────────
            # boot is cheap (format check + LibreOffice convert); no LLM involved
            booted: list[tuple[DocumentMail, _MailDocSpec]] = []

            for spec in all_specs:
                progress = self._progress_prefix(spec.doc_idx, overall_count)
                doc = DocumentMail(
                    source_file=spec.source_file,
                    working_directory=self._helper_file.generate_tempfolder(path_only=True),
                    helper_config=self._config,
                    llm_client=self._llm_client,
                    dms_client=self._dms_client,
                    ocr_client=self._ocr_client,
                    prompt_client=self._prompt_client,
                    forced_tags=spec.folder.tags,
                    forced_document_type=spec.folder.document_type,
                    forced_title=spec.forced_title,
                    forced_correspondent=spec.forced_correspondent,
                    forced_year=spec.forced_year,
                    forced_month=spec.forced_month,
                    forced_day=spec.forced_day,
                    fallback_title=spec.fallback_title,
                    fallback_correspondent=spec.fallback_correspondent,
                    fallback_year=spec.fallback_year,
                    fallback_month=spec.fallback_month,
                    fallback_day=spec.fallback_day,
                )
                try:
                    doc.boot()
                    self.logging.info("%s Booted '%s'.", progress, spec.source_file)
                    booted.append((doc, spec))
                except Exception as e:
                    self.logging.error(
                        "%s Failed to boot document '%s': %s",
                        progress, spec.source_file, e,
                    )
                    failed_messages.add(spec.message_id)
                    doc.cleanup()

            # ── Phase 1: upload — early gate before any expensive LLM work ────
            # uploading first lets us skip files the DMS already knows about
            # without spending Vision LLM time on them
            uploaded: list[tuple[DocumentMail, int, _MailDocSpec]] = []

            for doc, spec in booted:
                progress = self._progress_prefix(spec.doc_idx, overall_count)

                if spec.message_id in failed_messages:
                    # a sibling of this spec already failed — skip and clean up
                    doc.cleanup()
                    continue

                file_bytes = doc.get_file_bytes()
                file_name = os.path.basename(spec.source_file)

                try:
                    self.logging.info("%s Uploading '%s' to DMS.", progress, spec.source_file)
                    doc_id = await self._dms_client.do_upload_document(
                        file_bytes=file_bytes,
                        file_name=file_name,
                        owner_id=spec.owner_id,
                    )
                    uploaded.append((doc, doc_id, spec))
                except FileExistsError as e:
                    # duplicate detected — the content is already in the DMS from a previous run;
                    # mark as dup-only unless a sibling has already triggered a hard failure,
                    # in which case the message stays in the hard-fail bucket for retry
                    dup_id: int | None = e.args[0] if e.args else None
                    if dup_id is not None:
                        self.logging.warning(
                            "%s Skipping '%s': duplicate of DMS doc id=%d.",
                            progress, spec.source_file, dup_id, color="yellow",
                        )
                    else:
                        self.logging.warning(
                            "%s Skipping '%s': already exists in DMS.",
                            progress, spec.source_file, color="yellow",
                        )
                    # only promote to dup-only when no hard failure has already been recorded —
                    # a mixed message (some new, some dup) must remain a hard failure for retry
                    if spec.message_id not in failed_messages:
                        dup_only_messages.add(spec.message_id)
                    failed_messages.add(spec.message_id)
                    doc.cleanup()
                except Exception as e:
                    self.logging.error(
                        "%s Upload failed for '%s': %s",
                        progress, spec.source_file, e,
                    )
                    # real failure — demote from dup-only if it was previously recorded as such
                    dup_only_messages.discard(spec.message_id)
                    failed_messages.add(spec.message_id)
                    doc.cleanup()

            # ── Phase 2: load content — Vision LLM stays loaded for the batch ─
            content_docs: list[tuple[DocumentMail, int, _MailDocSpec]] = []

            for doc, doc_id, spec in uploaded:
                progress = self._progress_prefix(spec.doc_idx, overall_count)

                if spec.message_id in failed_messages:
                    # sibling failure discovered after upload — roll back this doc too
                    await self._rollback_document(doc_id)
                    doc.cleanup()
                    continue

                try:
                    self.logging.info("%s Loading content for '%s'.", progress, spec.source_file)
                    await doc.load_content()
                    content_docs.append((doc, doc_id, spec))
                except Exception as e:
                    self.logging.error(
                        "%s Failed to load content for '%s': %s",
                        progress, spec.source_file, e,
                    )
                    failed_messages.add(spec.message_id)
                    await self._rollback_document(doc_id)
                    doc.cleanup()

            # ── Phase 3: format content — Chat LLM cleans up OCR noise ────────
            formatted_docs: list[tuple[DocumentMail, int, _MailDocSpec]] = []

            for doc, doc_id, spec in content_docs:
                progress = self._progress_prefix(spec.doc_idx, overall_count)

                if spec.message_id in failed_messages:
                    await self._rollback_document(doc_id)
                    doc.cleanup()
                    continue

                try:
                    await doc.format_content()
                    formatted_docs.append((doc, doc_id, spec))
                except Exception as e:
                    self.logging.error(
                        "%s Failed to format content for '%s': %s",
                        progress, spec.source_file, e,
                    )
                    failed_messages.add(spec.message_id)
                    await self._rollback_document(doc_id)
                    doc.cleanup()

            # ── Phase 4: load metadata — hints flow forward across documents ──
            # Each document's extracted correspondents/types/tags are fed as hints
            # into the next document so the LLM reuses known values and does not
            # invent synonyms (e.g. "Rechnung" vs "Invoice" for the same concept).
            meta_docs: list[tuple[DocumentMail, int, _MailDocSpec]] = []
            new_tags: list[str] = []
            new_correspondents: list[str] = []
            new_doc_types: list[str] = []

            for doc, doc_id, spec in formatted_docs:
                progress = self._progress_prefix(spec.doc_idx, overall_count)

                if spec.message_id in failed_messages:
                    await self._rollback_document(doc_id)
                    doc.cleanup()
                    continue

                try:
                    self.logging.info("%s Extracting metadata for '%s'.", progress, spec.source_file)
                    # feed accumulated hints so the LLM prefers already-known values
                    doc.add_additional_correspondents(new_correspondents)
                    doc.add_additional_document_types(new_doc_types)
                    doc.add_additional_tags(new_tags)

                    await doc.load_metadata()
                    meta_docs.append((doc, doc_id, spec))

                    # read back so the next document benefits from this one's extractions
                    new_correspondents = doc.get_additional_correspondents()
                    new_doc_types = doc.get_additional_document_types()
                    new_tags = doc.get_additional_tags()
                except Exception as e:
                    self.logging.error(
                        "%s Failed to load metadata for '%s': %s",
                        progress, spec.source_file, e,
                    )
                    failed_messages.add(spec.message_id)
                    await self._rollback_document(doc_id)
                    doc.cleanup()

            # ── Phase 5: load tags — hint flow continues ───────────────────────
            tagged_docs: list[tuple[DocumentMail, int, _MailDocSpec]] = []

            for doc, doc_id, spec in meta_docs:
                progress = self._progress_prefix(spec.doc_idx, overall_count)

                if spec.message_id in failed_messages:
                    await self._rollback_document(doc_id)
                    doc.cleanup()
                    continue

                try:
                    self.logging.info("%s Extracting tags for '%s'.", progress, spec.source_file)
                    # re-apply accumulated tags before extraction — new_tags may have grown
                    # since load_metadata ran and load_tags benefits from the full picture
                    doc.add_additional_tags(new_tags)

                    await doc.load_tags()
                    tagged_docs.append((doc, doc_id, spec))

                    # update tag hints — load_tags may have discovered additional values
                    new_tags = doc.get_additional_tags()
                except Exception as e:
                    self.logging.error(
                        "%s Failed to load tags for '%s': %s",
                        progress, spec.source_file, e,
                    )
                    failed_messages.add(spec.message_id)
                    await self._rollback_document(doc_id)
                    doc.cleanup()

            # ── Phase 6: update DMS + mark messages in cache ──────────────────
            for doc, doc_id, spec in tagged_docs:
                progress = self._progress_prefix(spec.doc_idx, overall_count)

                if spec.message_id in failed_messages:
                    await self._rollback_document(doc_id)
                    doc.cleanup()
                    continue

                try:
                    await self._update_document_mail(
                        dms_doc_id=doc_id,
                        document=doc,
                        owner_id=spec.owner_id,
                        content_prefix=spec.content_prefix,
                        custom_fields=spec.custom_fields,
                    )
                    self.logging.info(
                        "%s '%s' ingested successfully -> DMS document id=%d",
                        progress, spec.source_file, doc_id,
                    )
                except Exception as e:
                    self.logging.error(
                        "%s Failed to update document id=%d ('%s'): %s",
                        progress, doc_id, spec.source_file, e,
                    )
                    failed_messages.add(spec.message_id)
                    await self._rollback_document(doc_id)
                finally:
                    # cleanup MUST always run — removes the working directory
                    doc.cleanup()

            # mark every message whose documents all succeeded (or were already in the DMS)
            # as processed in cache so they are skipped on the next run
            for message_id, indices in message_doc_map.items():
                # dup_only_messages is kept clean: any real error demotes a message out of it,
                # so membership here reliably means "all failures were FileExistsError"
                is_dup_only = message_id in dup_only_messages

                if message_id in failed_messages and not is_dup_only:
                    # genuine failure — the message must be retried on the next run
                    self.logging.warning(
                        "Message '%s' was not fully ingested — will be retried on next run.",
                        message_id,
                    )
                    continue

                # message_id IS the cache key (built by MailFetcher._get_cache_key) —
                # do not re-prefix it; MailFetcher.fetch_unprocessed reads the same key
                await self._cache_client.do_set(message_id, "1")

                if is_dup_only:
                    # all parts were already in the DMS — treat as done, no retry needed
                    self.logging.info(
                        "Message '%s' already ingested (all parts were duplicates) — marked as processed.",
                        message_id, color="blue",
                    )
                else:
                    self.logging.info(
                        "Message '%s' fully ingested (%d document(s)) — marked as processed.",
                        message_id, len(indices),
                    )

        finally:
            # delete all source temp files — doc.cleanup() only handles working directories
            for spec in all_specs:
                try:
                    os.unlink(spec.source_file)
                except OSError:
                    pass

    ##########################################
    ################# DMS ####################
    ##########################################

    async def _update_document_mail(
        self,
        dms_doc_id: int,
        document: DocumentMail,
        owner_id: int,
        content_prefix: str | None = None,
        custom_fields: dict[str, str] | None = None,
    ) -> None:
        """Update a DMS document with metadata extracted from a mail message.

        All title, correspondent, date, document_type, and tag logic is now encapsulated
        inside DocumentMail — forced and fallback values were injected at construction time
        and are resolved transparently via the public getters. This method is a thin adapter
        that reads the final values from the Document and writes them to the DMS.

        Args:
            dms_doc_id: DMS document ID from the upload step.
            document: Fully processed Document instance.
            owner_id: DMS owner_id for the update request.
            content_prefix: Text prepended to document content before the DMS update
                (e.g. the mail context header for attachment documents).
            custom_fields: Arbitrary DMS custom fields to set; keys are field names,
                values are the string values. None or empty means no custom field update.

        Raises:
            Exception: Propagates any DMS resolve/update error to the caller.
        """
        meta = document.get_metadata()
        tags = document.get_tags()

        # prepend mail context header when provided — first Qdrant chunk then carries
        # sender + subject so the attachment is findable via mail-level queries
        raw_content = document.get_content()
        content = (content_prefix + "\n\n" + raw_content) if content_prefix else raw_content

        # title, correspondent, document_type, and date are fully resolved inside DocumentMail —
        # forced values won over the LLM, fallback values filled in where the LLM found nothing
        title = document.get_title()
        correspondent_str = meta.correspondent or ""
        document_type_str = meta.document_type or ""
        date_string = document.get_date_string(pattern="%Y-%m-%d")

        # tags are already merged — forced_tags (folder hints) merged with LLM tags inside get_tags()

        # resolve or create DMS entities — let errors propagate for rollback
        correspondent_id: int | None = None
        document_type_id: int | None = None
        tag_ids: list[int] = []

        if correspondent_str:
            try:
                correspondent_id = await self._dms_client.do_resolve_or_create_correspondent(correspondent_str)
            except Exception as e:
                self.logging.warning("Failed to resolve correspondent '%s': %s", correspondent_str, e)
                raise

        if document_type_str:
            try:
                document_type_id = await self._dms_client.do_resolve_or_create_document_type(document_type_str)
            except Exception as e:
                self.logging.warning("Failed to resolve document_type '%s': %s", document_type_str, e)
                raise

        for tag_name in tags:
            try:
                tag_id = await self._dms_client.do_resolve_or_create_tag(tag_name)
                tag_ids.append(tag_id)
            except Exception as e:
                self.logging.warning("Failed to resolve tag '%s': %s", tag_name, e)
                raise

        await self._dms_client.do_update_document(
            document_id=dms_doc_id,
            update=DocumentUpdateRequest(
                title=title,
                correspondent_id=correspondent_id,
                document_type_id=document_type_id,
                tag_ids=tag_ids,
                content=content,
                created_date=date_string,
                owner_id=owner_id,
                custom_fields=custom_fields or {},   # pass through directly — interface resolves names to IDs
            ),
        )
        self.logging.info(
            "Mail document id=%d updated — title='%s', correspondent='%s', type='%s'.",
            dms_doc_id, title, correspondent_str, document_type_str,
        )

    async def _rollback_document(self, doc_id: int) -> None:
        """Delete a DMS document that was uploaded but whose pipeline then failed.

        Args:
            doc_id: The DMS document ID to delete.
        """
        try:
            await self._dms_client.do_delete_document(doc_id)
            self.logging.warning("Rolled back DMS document id=%d.", doc_id, color="yellow")
        except Exception as e:
            self.logging.error("Rollback: failed to delete DMS document id=%d: %s", doc_id, e)