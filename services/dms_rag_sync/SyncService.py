"""Synchronisation service.

Reads all documents from each DMS client, splits their text into chunks,
generates embeddings via an LLMClient, and upserts the resulting vectors
into each RAG backend with a generic metadata payload.
"""

import asyncio
import hashlib
import uuid

from shared.clients.cache.CacheClientInterface import CacheClientInterface, KEY_FILTER_OPTIONS
from shared.clients.dms.DMSClientInterface import DMSClientInterface
from shared.clients.dms.models.Document import DocumentHighDetails
from shared.clients.rag.RAGClientInterface import RAGClientInterface
from shared.clients.rag.models.Point import PointHighDetailsRequest, PointUpsert
from shared.clients.llm.LLMClientInterface import LLMClientInterface
from shared.helper.HelperConfig import HelperConfig

UPSERT_BATCH_SIZE = 100 # max points per RAG upsert call
DOC_CONCURRENCY = 5     # max parallel document syncs


def _compute_doc_hash(doc: DocumentHighDetails) -> str:
    """Compute a SHA-256 fingerprint over the fields that affect a document's vectors.

    Hashing content, title, owner, tags, correspondent, and document type means
    any change to these fields produces a different hash and triggers a re-sync.
    The hash is the same for every chunk of the same document.

    Args:
        doc (DocumentHighDetails): The enriched document to fingerprint.

    Returns:
        str: Hex-encoded SHA-256 digest.
    """
    parts = [
        doc.content or "",
        doc.title or "",
        str(doc.owner_id or ""),
        ",".join(sorted(str(t) for t in (doc.tag_ids or []))),
        str(doc.correspondent_id or ""),
        str(doc.document_type_id or ""),
    ]
    return hashlib.sha256("|".join(parts).encode()).hexdigest()


def _make_point_id(engine: str, doc_id: str, chunk_index: int) -> str:
    """Build a deterministic UUID5 point ID for a RAG vector.

    Using UUID5 ensures the same document chunk always maps to the same
    point ID so that re-syncing overwrites rather than duplicates.

    Args:
        engine (str): DMS engine identifier (e.g. "paperless").
        doc_id (str): Document ID in the DMS.
        chunk_index (int): Zero-based chunk index within the document.

    Returns:
        str: UUID string usable as a RAG point ID.
    """
    return str(uuid.uuid5(uuid.NAMESPACE_OID, f"{engine}:{doc_id}:{chunk_index}"))


class SyncService:
    """Orchestrates the full sync pipeline from DMSs to RAGs."""

    def __init__(
        self,
        helper_config: HelperConfig,
        dms_clients: list[DMSClientInterface],
        rag_clients: list[RAGClientInterface],
        embed_client: LLMClientInterface,
        cache_client: CacheClientInterface,
    ) -> None:
        self.logging = helper_config.get_logger()
        self._dms_clients = dms_clients
        self._rag_clients = rag_clients
        self._embed_client = embed_client
        self._cache_client = cache_client

    ##########################################
    ############### CORE SYNC ################
    ##########################################

    async def do_full_sync(self) -> None:
        """Sync all DMS clients to all RAG clients."""
        self.logging.info("Starting full sync...")

        for dms_client in self._dms_clients:
            for rag_client in self._rag_clients:
                await self.do_sync(rag_client, dms_client)

    async def do_incremental_sync(self, document_id: int, engine: str) -> None:
        """Fetch and re-sync a single document identified by its DMS ID and engine name.

        Called by the webhook handler after a DMS signals that a document has been
        created or updated. The ``engine`` parameter identifies exactly which DMS
        client to use; if no registered client matches, the method logs an error
        and returns immediately without touching any RAG backend.

        The matching DMS client re-fetches the document, resolves its metadata from
        the existing cache, builds a DocumentHighDetails object, and passes it to
        _sync_document with an empty rag_hashes dict so that the document is always
        re-embedded regardless of its stored content hash.

        Errors for individual RAG combinations are caught and logged so that the
        webhook can always return 202 Accepted.

        Args:
            document_id (int): DMS document ID to sync.
            engine (str): DMS engine name (e.g. "paperless") that identifies which
                registered DMS client should handle this document. Must match the
                value returned by ``DMSClientInterface.get_engine_name()``.
        """
        self.logging.info("Starting incremental sync for document id=%d...", document_id)
        sem = asyncio.Semaphore(DOC_CONCURRENCY)

        dms_client = next(
            (c for c in self._dms_clients if c.get_engine_name().lower() == engine.lower()), None
        )
        if dms_client is None:
            self.logging.error(
                "Incremental sync: no DMS client registered for engine '%s'.", engine
            )
            return

        try:
            # ensure reference caches are populated (no-op if already filled)
            await dms_client.fill_cache(force_refresh=False)

            # fetch fresh document details from the DMS
            doc_details = await dms_client.do_fetch_document_details(str(document_id))

            # resolve metadata from caches
            correspondent = None
            owner = None
            tags = []
            document_type = None

            if doc_details.correspondent_id and dms_client._cache_correspondents:
                correspondent = dms_client._cache_correspondents.get(doc_details.correspondent_id)
            if doc_details.owner_id and dms_client._cache_owners:
                owner = dms_client._cache_owners.get(doc_details.owner_id)
            if doc_details.tag_ids and dms_client._cache_tags:
                for tag_id in doc_details.tag_ids:
                    tag = dms_client._cache_tags.get(tag_id)
                    if tag:
                        tags.append(tag)
            if doc_details.document_type_id and dms_client._cache_document_types:
                document_type = dms_client._cache_document_types.get(doc_details.document_type_id)

            doc = DocumentHighDetails(
                **doc_details.model_dump(),
                correspondent=correspondent,
                owner=owner,
                tags=tags,
                document_type=document_type,
            )
        except Exception as e:
            self.logging.error(
                "Incremental sync: failed to fetch/build document id=%d from DMS '%s': %s",
                document_id, engine, e,
            )
            return

        # look up the old owner_id from RAG before syncing so we can detect
        # owner loss or change and invalidate the previous cache entry afterwards
        old_owner_id: int | None = await self._fetch_owner_of_document(
            self._rag_clients[0], engine, str(document_id)
        )

        for rag_client in self._rag_clients:
            try:
                result = await self._sync_document(doc, rag_client, dms_client, sem, {})
                if result:
                    self.logging.info(
                        "Incremental sync complete for document id=%d in DMS '%s' → RAG '%s'.",
                        document_id, engine, rag_client.get_engine_name(),
                    )
                else:
                    self.logging.info(
                        "Incremental sync: document id=%d in DMS '%s' was skipped (no content or missing owner_id).",
                        document_id, engine,
                    )
            except Exception as e:
                self.logging.error(
                    "Incremental sync: failed to sync document id=%d to RAG '%s': %s",
                    document_id, rag_client.get_engine_name(), e,
                )

        # update filter cache — pass the old owner_id so a lost/changed owner
        # causes the previous cache entry to be invalidated
        await self._merge_filter_cache(doc, old_owner_id=old_owner_id)

    async def do_sync(self, rag_client: RAGClientInterface, dms_client: DMSClientInterface) -> None:
        """Sync documents from a single DMS client to a single RAG client.

        Args:
            rag_client (RAGClientInterface): The RAG client to sync to.
            dms_client (DMSClientInterface): The DMS client to sync from.

        Raises:
            Exception: If any step in the sync process fails critically.
        """
        engine = dms_client.get_engine_name()
        self.logging.info("Syncing DMS '%s' → RAG '%s'", engine, rag_client.get_engine_name())

        # fill the dms cache (all reference data + enriched documents)
        await dms_client.fill_cache()

        enriched_docs = dms_client.get_enriched_documents()
        if not enriched_docs:
            self.logging.warning("No documents found in DMS cache for engine '%s'. Skipping.", engine)
            return

        self.logging.info("Processing %d documents from '%s'...", len(enriched_docs), engine)

        # load existing content hashes from the RAG backend once before the sync loop
        rag_hashes = await self._load_rag_hashes(rag_client, engine)

        # process documents concurrently with bounded parallelism
        sem = asyncio.Semaphore(DOC_CONCURRENCY)
        results = await asyncio.gather(
            *[
                self._sync_document(doc, rag_client, dms_client, sem, rag_hashes)
                for doc in enriched_docs
            ],
            return_exceptions=True,
        )

        synced = sum(1 for r in results if r is True)
        skipped = sum(1 for r in results if r is False)
        errors = sum(1 for r in results if isinstance(r, Exception))
        color = "green" if errors == 0 and skipped == 0 else "yellow" if errors == 0 else "red"
        self.logging.info(
            "Sync complete for '%s': %d synced, %d skipped, %d errors.",
            engine, synced, skipped, errors, color=color
        )

        # clean up orphaned vectors in the RAG backend
        dms_ids = {doc.id for doc in enriched_docs}
        await self._cleanup_orphans(rag_client, engine, dms_ids)

        # rebuild filter option cache from the already-fetched document data
        await self._update_filter_cache(enriched_docs)

    ##########################################
    ############ DOCUMENT SYNC ###############
    ##########################################

    async def _load_rag_hashes(
        self,
        rag_client: RAGClientInterface,
        engine: str,
    ) -> dict[str, str]:
        """Scroll the RAG backend once and build a {dms_doc_id: content_hash} map.

        Used before the sync loop so that unchanged documents can be skipped without
        hitting the embedding API. Only points that carry a non-None content_hash are
        included; points written by an older schema version without a hash are treated
        as unknown and will be re-synced on the next run.

        On any scroll error an empty dict is returned so that every document is treated
        as new — a safe fallback that avoids skipping documents due to a transient error.

        Args:
            rag_client (RAGClientInterface): The RAG client to query.
            engine (str): DMS engine identifier used as a scroll filter.

        Returns:
            dict[str, str]: Mapping of dms_doc_id → content_hash for all known points.
        """
        try:
            points = await rag_client.do_fetch_points(
                filters=[{"key": "dms_engine", "match": {"value": engine}}],
                include_fields=["dms_doc_id", "content_hash"],
                with_vector=False,
            )
        except Exception as e:
            self.logging.error(
                "Failed to load RAG hashes for engine '%s': %s. All documents will be re-synced.",
                engine, e,
            )
            return {}

        hashes: dict[str, str] = {}
        for point in points:
            doc_id = point.dms_doc_id
            content_hash = point.content_hash
            if doc_id and content_hash is not None:
                hashes[doc_id] = content_hash
        return hashes

    async def _delete_document_vectors(
        self,
        rag_client: RAGClientInterface,
        engine: str,
        doc_id: str,
    ) -> None:
        """Delete all RAG vectors for a single document.

        Silently swallows errors after logging them so that callers can
        continue without raising.

        Args:
            rag_client (RAGClientInterface): The RAG client to delete from.
            engine (str): DMS engine identifier used in the filter.
            doc_id (str): Document ID whose vectors should be removed.
        """
        try:
            delete_filter = {
                "must": [
                    {"key": "dms_engine", "match": {"value": engine}},
                    {"key": "dms_doc_id", "match": {"value": doc_id}},
                ]
            }
            success = await rag_client.do_delete_points_by_filter(delete_filter)
            if not success:
                raise Exception("RAG client reported delete failure for document id=%s." % doc_id)
        except Exception as e:
            self.logging.error(
                "Failed to delete vectors for document id=%s: %s", doc_id, e
            )

    async def _validate_document(
        self,
        doc: DocumentHighDetails,
        rag_client: RAGClientInterface,
        engine: str,
    ) -> list[str] | None:
        """Validate a document and return its text chunks if it is syncable.

        Performs all pre-sync checks in order. On the first failed check the
        existing RAG vectors for the document are deleted and ``None`` is
        returned so that ``_sync_document`` can exit early.

        Args:
            doc (DocumentHighDetails): The enriched document to validate.
            rag_client (RAGClientInterface): The RAG client used for cleanup on failure.
            engine (str): DMS engine identifier (used in delete filter and logging).

        Returns:
            list[str]: Non-empty list of text chunks when all checks pass.
            None: When any check fails (vectors already cleaned up).
        """
        # security invariant: documents without owner_id must never be indexed
        if not doc.owner_id:
            self.logging.warning(
                "Skipping document id=%s: missing owner_id (security invariant).", doc.id, color="yellow"
            )
            await self._delete_document_vectors(rag_client, engine, doc.id)
            return None

        # skip documents without OCR text
        if not doc.content or not doc.content.strip():
            self.logging.warning("Skipping document id=%s ('%s'): no content.", doc.id, doc.title, color="yellow")
            await self._delete_document_vectors(rag_client, engine, doc.id)
            return None

        chunks = rag_client.split_text(doc.content)
        if not chunks:
            self.logging.warning(
                "Skipping document id=%s ('%s'): content produced no chunks.", doc.id, doc.title, color="yellow"
            )
            await self._delete_document_vectors(rag_client, engine, doc.id)
            return None

        return chunks

    async def _sync_document(
        self,
        doc: DocumentHighDetails,
        rag_client: RAGClientInterface,
        dms_client: DMSClientInterface,
        sem: asyncio.Semaphore,
        rag_hashes: dict[str, str],
    ) -> bool:
        """Embed and upsert a single document's chunks into the RAG backend.

        Compares the document's content hash against the stored hash in the RAG
        backend. If they match the document is unchanged and embedding is skipped.

        Args:
            doc (DocumentHighDetails): The enriched document to sync.
            rag_client (RAGClientInterface): The RAG client to write to.
            dms_client (DMSClientInterface): Used for engine name in point IDs.
            sem (asyncio.Semaphore): Concurrency limiter.
            rag_hashes (dict[str, str]): Pre-loaded {dms_doc_id: content_hash} map.

        Returns:
            bool: True if synced successfully, False if skipped.

        Raises:
            Exception: Propagated to gather() if embedding or upsert fails.
        """
        async with sem:
            engine = dms_client.get_engine_name()

            chunks = await self._validate_document(doc, rag_client, engine)
            if chunks is None:
                return False

            current_hash = _compute_doc_hash(doc)
            if rag_hashes.get(str(doc.id)) == current_hash:
                self.logging.debug(
                    "Skipping document id=%s ('%s'): unchanged since last sync.", doc.id, doc.title
                )
                return False

            try:
                # batch embed — one HTTP request for all chunks of this document
                vectors = await self._embed_client.do_embed(texts=chunks)
            except Exception as e:
                self.logging.error(
                    "Embedding failed for document id=%s ('%s'): %s", doc.id, doc.title, e, color="red"
                )
                raise

            # delete stale chunks for this document before upserting the new ones
            try:
                delete_filter = {
                    "must": [
                        {"key": "dms_engine", "match": {"value": engine}},
                        {"key": "dms_doc_id", "match": {"value": doc.id}},
                    ]
                }
                success = await rag_client.do_delete_points_by_filter(delete_filter)
                if not success:
                    raise Exception("RAG client reported delete failure for document id=%s." % doc.id)
            except Exception as e:
                self.logging.error(
                    "Delete-before-upsert failed for document id=%s: %s", doc.id, e, color="red"
                )
                raise

            # build RAG points
            points: list[PointUpsert] = []
            for chunk_index, (chunk, vector) in enumerate(zip(chunks, vectors)):
                payload = PointHighDetailsRequest(
                    dms_engine=engine,
                    dms_doc_id=doc.id,
                    chunk_index=chunk_index,
                    title=doc.title or "",
                    label_ids=doc.tag_ids,
                    category_id=doc.correspondent_id,
                    type_id=doc.document_type_id,
                    owner_id=doc.owner_id,
                    label_names=[t.name for t in (doc.tags or []) if t.name],
                    category_name=doc.correspondent.name if doc.correspondent else None,
                    type_name=doc.document_type.name if doc.document_type else None,
                    owner_username=doc.owner.username if doc.owner else None,
                    created=doc.created_date.isoformat() if doc.created_date else None,
                    chunk_text=chunk,
                    content_hash=current_hash,
                )
                points.append(PointUpsert(
                    id=_make_point_id(engine, doc.id, chunk_index),
                    vector=vector,
                    payload=payload,
                ))

            # upsert in batches to avoid oversized requests
            try:
                for batch_start in range(0, len(points), UPSERT_BATCH_SIZE):
                    batch = points[batch_start: batch_start + UPSERT_BATCH_SIZE]
                    success = await rag_client.do_upsert_points(batch)
                    if not success:
                        raise Exception("RAG client reported upsert failure for batch starting at index %d." % batch_start)
            except Exception as e:
                self.logging.error(
                    "Upsert failed for document id=%s: %s", doc.id, e, color="red"
                )
                raise

            self.logging.info("Synced document id=%s ('%s'): %d chunks upserted.", doc.id, doc.title, len(points), color="green")
            return True

    ##########################################
    ############ ORPHAN CLEANUP ##############
    ##########################################

    async def _cleanup_orphans(
        self,
        rag_client: RAGClientInterface,
        engine: str,
        dms_ids: set[str],
    ) -> None:
        """Remove RAG vectors whose dms_doc_id no longer exists in the DMS.

        Scrolls only the points belonging to the given engine and deletes any
        whose dms_doc_id is absent from the current DMS document set.

        Args:
            rag_client (RAGClientInterface): The RAG client to clean up.
            engine (str): DMS engine identifier to scope the cleanup.
            dms_ids (set[str]): Set of valid dms_doc_ids currently in the DMS.
        """
        self.logging.info(
            "Starting orphan cleanup for engine '%s' (scrolling RAG for stale document IDs)...",
            engine,
        )

        rag_doc_ids: set[str] = set()
        try:
            points = await rag_client.do_fetch_points(
                filters=[{"key": "dms_engine", "match": {"value": engine}}],
                include_fields=["dms_doc_id"],
                with_vector=False,
            )
            for point in points:
                rag_doc_ids.add(point.dms_doc_id)
        except Exception as e:
            self.logging.error("Orphan cleanup scroll failed: %s. Skipping cleanup.", e, color="red")
            return

        orphan_ids = rag_doc_ids - dms_ids
        if not orphan_ids:
            self.logging.info("Orphan cleanup: no stale documents found.", color="green")
            return

        self.logging.info("Orphan cleanup: removing vectors for %d stale document(s).", len(orphan_ids))
        removed = 0
        for orphan_id in orphan_ids:
            try:
                delete_filter = {
                    "must": [
                        {"key": "dms_engine", "match": {"value": engine}},
                        {"key": "dms_doc_id", "match": {"value": orphan_id}},
                    ]
                }
                success = await rag_client.do_delete_points_by_filter(delete_filter)
                if not success:
                    raise Exception("RAG client reported delete failure for document id=%s." % orphan_id)
                removed += 1
            except Exception as e:
                self.logging.error(
                    "Orphan cleanup: failed to delete vectors for engine='%s' dms_doc_id=%s: %s",
                    engine, orphan_id, e, color="red"
                )

        self.logging.info("Orphan cleanup complete: removed vectors for %d document(s).", removed, color="green")

    ##########################################
    ############# HELPERS ####################
    ##########################################

    async def _fetch_owner_of_document(
        self,
        rag_client: RAGClientInterface,
        engine: str,
        doc_id: str,
    ) -> int | None:
        """Return the owner_id stored in RAG for a document before syncing.

        Used by do_incremental_sync() to detect owner loss or change so the old
        owner's filter cache entry can be invalidated.

        Args:
            rag_client: The RAG client to query.
            engine: DMS engine identifier used in the filter.
            doc_id: Document ID whose current RAG owner_id to retrieve.

        Returns:
            The stored owner_id as int, or None if not found or on error.
        """
        try:
            points = await rag_client.do_fetch_points(
                filters=[
                    {"key": "dms_engine", "match": {"value": engine}},
                    {"key": "dms_doc_id", "match": {"value": doc_id}},
                ],
                include_fields=["owner_id"],
                with_vector=False,
            )
            if points:
                return int(points[0].owner_id)
        except Exception as e:
            self.logging.warning(
                "Could not fetch current owner_id for document id=%s from RAG: %s", doc_id, e
            )
        return None

    async def _merge_filter_cache(
        self,
        doc: DocumentHighDetails,
        old_owner_id: int | None = None,
    ) -> None:
        """Merge a single document's metadata values into the owner's filter cache entry.

        Reads the existing cached options for the new owner, adds any new values
        from the document, and writes back. If the owner changed or was removed,
        the old owner's cache entry is invalidated so SearchService rebuilds it.

        This is intentionally additive only: values removed from a document via an
        update are not pruned here. The next full sync rebuilds the cache from scratch
        and corrects any stale entries.

        Args:
            doc: The enriched document that was just synced.
            old_owner_id: The owner_id the document had in RAG before this sync,
                          used to invalidate the previous cache entry on owner change.
        """
        # invalidate old owner's cache if the owner changed or was lost
        if old_owner_id is not None and old_owner_id != doc.owner_id:
            try:
                old_key = "%s:%s:%d" % (KEY_FILTER_OPTIONS, doc.engine, old_owner_id)
                await self._cache_client.do_delete(old_key)
                self.logging.debug(
                    "Filter cache invalidated for engine=%s, old owner_id=%d after owner change on document id=%s.",
                    doc.engine, old_owner_id, doc.id,
                )
            except Exception as e:
                self.logging.warning(
                    "Failed to invalidate old filter cache for engine=%s, owner_id=%d: %s",
                    doc.engine, old_owner_id, e,
                )

        if not doc.owner_id:
            return

        cache_key = "%s:%s:%d" % (KEY_FILTER_OPTIONS, doc.engine, doc.owner_id)
        try:
            existing = await self._cache_client.do_get_json(cache_key) or {}
            correspondents: set[str] = set(existing.get("correspondents") or [])
            document_types: set[str] = set(existing.get("document_types") or [])
            tags: set[str] = set(existing.get("tags") or [])

            if doc.correspondent and doc.correspondent.name:
                correspondents.add(doc.correspondent.name)
            if doc.document_type and doc.document_type.name:
                document_types.add(doc.document_type.name)
            for tag in (doc.tags or []):
                if tag.name:
                    tags.add(tag.name)

            options = {
                "correspondents": sorted(correspondents),
                "document_types": sorted(document_types),
                "tags": sorted(tags),
            }
            await self._cache_client.do_set_json(cache_key, options)
            self.logging.debug(
                "Filter cache merged for engine=%s, owner_id=%d after incremental sync of document id=%s.",
                doc.engine, doc.owner_id, doc.id,
            )
        except Exception as e:
            self.logging.warning(
                "Failed to merge filter cache for owner_id=%d: %s", doc.owner_id, e
            )

    async def _update_filter_cache(self, enriched_docs: list[DocumentHighDetails]) -> None:
        """Build per-owner filter options from enriched documents and write to cache.

        Groups documents by owner_id, collects distinct correspondent names,
        document type names, and tag names, then writes one cache entry per owner.
        This is called after a full sync when all DMS data is already in memory.

        Args:
            enriched_docs: All enriched documents returned by the DMS client.
        """
        # wipe all existing filter option entries before rebuilding from scratch
        # so that owners who have lost all their documents are cleaned up
        try:
            await self._cache_client.do_delete_pattern("%s:*" % KEY_FILTER_OPTIONS)
        except Exception as e:
            self.logging.warning("Failed to clear filter cache before full rebuild: %s", e)

        by_engine_owner: dict[tuple[str, int], dict] = {}
        for doc in enriched_docs:
            if not doc.owner_id:
                continue
            key = (doc.engine, doc.owner_id)
            if key not in by_engine_owner:
                by_engine_owner[key] = {
                    "correspondents": set(),
                    "document_types": set(),
                    "tags": set(),
                }
            if doc.correspondent and doc.correspondent.name:
                by_engine_owner[key]["correspondents"].add(doc.correspondent.name)
            if doc.document_type and doc.document_type.name:
                by_engine_owner[key]["document_types"].add(doc.document_type.name)
            for tag in (doc.tags or []):
                if tag.name:
                    by_engine_owner[key]["tags"].add(tag.name)

        for (engine_name, oid), opts in by_engine_owner.items():
            options = {
                "correspondents": sorted(opts["correspondents"]),
                "document_types": sorted(opts["document_types"]),
                "tags": sorted(opts["tags"]),
            }
            try:
                cache_key = "%s:%s:%d" % (KEY_FILTER_OPTIONS, engine_name, oid)
                await self._cache_client.do_set_json(cache_key, options)
                self.logging.debug(
                    "Filter cache updated for engine=%s, owner_id=%d: %d correspondents, "
                    "%d document_types, %d tags.",
                    engine_name, oid,
                    len(options["correspondents"]),
                    len(options["document_types"]),
                    len(options["tags"]),
                )
            except Exception as e:
                self.logging.warning(
                    "Failed to write filter cache for engine=%s, owner_id=%d: %s",
                    engine_name, oid, e,
                )
