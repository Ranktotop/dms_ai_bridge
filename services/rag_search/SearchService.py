import asyncio
import json
from dataclasses import dataclass, field

from shared.clients.cache.CacheClientInterface import CacheClientInterface, KEY_FILTER_OPTIONS
from shared.clients.llm.LLMClientInterface import LLMClientInterface
from shared.clients.rag.RAGClientInterface import RAGClientInterface
from shared.clients.dms.DMSClientInterface import DMSClientInterface
from shared.clients.rag.models.Point import PointHighDetails
from services.rag_search.helper.IdentityHelper import IdentityHelper
from services.rag_search.helper.LLMHelper import LLMHelper
from services.rag_search.helper.CacheHelper import CacheHelper
from shared.helper.HelperConfig import HelperConfig
from shared.clients.rag.models.Document import DocumentBase


@dataclass
class SearchResult:
    dms_doc_id: str
    title: str
    score: float
    chunk_text: str | None = None
    created: str | None = None
    category_name: str | None = None
    type_name: str | None = None
    label_names: list[str] = field(default_factory=list)

class SearchService:
    """Framework-agnostic semantic search: embed -> classify -> scroll -> domain results.

    No FastAPI or Pydantic response model imports — can be consumed by any
    entry point (FastAPI router, CLI, tests).
    """

    def __init__(
        self,
        helper_config: HelperConfig,
        rag_clients: list[RAGClientInterface],
        llm_client: LLMClientInterface,
        cache_client: CacheClientInterface,
        dms_clients: list[DMSClientInterface]
    ) -> None:
        self.logging = helper_config.get_logger()
        self._helper_config = helper_config
        self._rag_clients = rag_clients
        self._dms_clients = dms_clients
        self._llm_client = llm_client
        self._cache_client = cache_client
        self._llmhelper = LLMHelper(llm_client=llm_client, config=helper_config)
        self._cache_helper = CacheHelper(cache_client=cache_client, rag_clients=rag_clients, config=helper_config)

    ##########################################
    ############### CORE #####################
    ##########################################

    async def do_search(
        self,
        query: str,
        identity_helper: IdentityHelper,
        chat_history: list[dict] | None = None,
        limit: int = -1,
        merge_results: bool = False,
    ) -> list[PointHighDetails]:
        """Embed a query, classify it, scroll the RAG backend, and return domain results.

        Searches across all provided (owner_id, engine) pairs so that a single
        frontend user whose documents span multiple DMS backends is served by one
        call.  Each pair produces a combined (dms_engine + owner_id) condition;
        multiple pairs are wrapped in a RAG `should` clause so that documents
        from any of the caller's identities are returned.

        Phase III: uses do_search() with payload filters.  Returns an empty list
        when classification filters yield no results — the caller (LLM frontend /
        Phase IV agent) is responsible for asking the user to clarify.

        Args:
            query: The natural language query string.
            identity_helper: IdentityHelper instance containing resolved identities for the user.
            chat_history: Optional prior conversation turns for context-aware classification.
            limit: Max number of results to return. -1 for no limit (default). Note that the limit is applied after sorting by score
            merge_results: If True, chunks from the same document will be merged into a single PointHighDetails entry with concatenated chunk_text.
        Returns:
            List of PointHighDetails domain objects.
        """
        # get data from cache or rag for all identities
        ragResultsForIdentities = [await self._cache_helper.get_data(identity.dms_engine, identity.owner_id) for identity in identity_helper.get_identities()]
        
        # classify the query to extract metadata filters
        classification = await self._llmhelper._classify_query(query=query, rag_data=ragResultsForIdentities, chat_history=chat_history)
        self.logging.debug(
            "Classification: correspondent=%s, document_type=%s, tags=%s",
            classification.correspondent, classification.document_type, classification.tags,
        )

        # vectorize the plain query
        vectors = await self._llm_client.do_embed([query])
        query_vector = vectors[0]
        self.logging.debug("Query vector dimension: %d", len(query_vector))

        # Build identity-aware filter conditions
        identities = identity_helper.get_identities()
        if len(identities) == 1:
            identity = identities[0]
            must_conditions: list[dict] = [
                {"key": "dms_engine", "match": {"value": identity.dms_engine}},
                {"key": "owner_id",   "match": {"value": str(identity.owner_id)}},
            ]
        else:
            must_conditions = [
                {
                    "should": [
                        {
                            "must": [
                                {"key": "dms_engine", "match": {"value": i.dms_engine}},
                                {"key": "owner_id",   "match": {"value": str(i.owner_id)}},
                            ]
                        }
                        for i in identities
                    ]
                }
            ]

        # Append classification-derived filters
        if classification.correspondent:
            must_conditions.append(
                {"key": "category_name", "match": {"value": classification.correspondent}}
            )
        if classification.document_type:
            must_conditions.append(
                {"key": "type_name", "match": {"value": classification.document_type}}
            )
        for tag in classification.tags:
            must_conditions.append(
                {"key": "label_names", "match": {"value": tag}}
            )
        filters = {"must": must_conditions}

        # Vector similarity search — if full filter yields 0, retry without type/tag conditions
        # (correspondent remains mandatory as it anchors the identity of the document owner)
        results = await self._execute_search(query_vector, filters)

        search_name = "exact"
        if not results and (classification.document_type or classification.tags):
            search_name = "relaxed"
            fallback_conditions = [
                c for c in must_conditions
                if c.get("key") not in ("type_name", "label_names")
            ]
            self.logging.debug(
                "Search in RAG returned 0 results using filters %s. Retrying with %s filters (removed type and tags).",
                filters, search_name,
            )
            results = await self._execute_search(
                query_vector, {"must": fallback_conditions}
            )

        self.logging.info("Search in RAG (%s) returned %d result(s).", search_name, len(results))
        # sort results by score desc and apply limit
        results.sort(key=lambda r: r.score, reverse=True)
        if limit > 0 and len(results) > limit:
            self.logging.info("Fetched more results than requested. Returning only top %d results of %d.", limit, len(results))
            results = results[:limit]    

        # if merging is requested
        if merge_results:
            return self._merge_points(results)    
        return results

    async def do_fetch_by_doc_id(
        self,
        doc_id: str,
        dms_engine: str,
        owner_id: str,
    ) -> list[PointHighDetails]:
        """Fetch all chunks for a specific document by its DMS document ID.

        Uses do_fetch_points() with exact-match filters — deterministic, no vector search.
        Returns an empty list if the document is not found or not owned by owner_id.

        Args:
            doc_id: DMS document ID (string).
            dms_engine: DMS engine name (e.g. "paperless").
            owner_id: Owner ID as string — enforced for access isolation.

        Returns:
            List of PointHighDetails for all chunks of the document.
        """
        filters = [
            {"key": "dms_doc_id", "match": {"value": doc_id}},
            {"key": "dms_engine", "match": {"value": dms_engine}},
            {"key": "owner_id",   "match": {"value": owner_id}},
        ]
        fetch_tasks = [
            rag_client.do_fetch_points(
                filters=filters,
                include_fields=True,
                with_vector=False,
            )
            for rag_client in self._rag_clients
        ]
        responses = await asyncio.gather(*fetch_tasks, return_exceptions=True)

        results: list[PointHighDetails] = []
        for idx, points in enumerate(responses):
            if isinstance(points, Exception):
                self.logging.warning(
                    "SearchService.do_fetch_by_doc_id: RAG client [%d] failed: %s",
                    idx, points,
                )
                continue
            results.extend(points)
        results.sort(key=lambda r: r.chunk_index or 0)
        return results
    
    async def do_fetch_full_by_doc_id(
        self,
        doc_id: str,
        dms_engine: str,
        owner_id: str,
    ) -> list[DocumentBase]:
        """Fetches the full content for a specific document by its DMS document ID.

        Uses do_reconstruct_document_content() with exact-match filters — deterministic, no vector search.
        Returns an empty list if the document is not found or not owned by owner_id.

        Args:
            doc_id: DMS document ID (string).
            dms_engine: DMS engine name (e.g. "paperless").
            owner_id: Owner ID as string — enforced for access isolation.

        Returns:
            list[DocumentBase]: List of reconstructed documents for all RAG clients.
        """
        fetch_tasks = [
            rag_client.do_reconstruct_document_content(
                dms_engine=dms_engine,
                dms_doc_id=doc_id,
                owner_id=owner_id
            )
            for rag_client in self._rag_clients
        ]
        responses = await asyncio.gather(*fetch_tasks, return_exceptions=True)

        results: list[DocumentBase] = []
        for idx, doc in enumerate(responses):
            if isinstance(doc, Exception):
                self.logging.warning(
                    "SearchService.do_fetch_full_by_doc_id: RAG client [%d] failed: %s",
                    idx, doc,
                )
                continue
            #skip empty results
            if doc.content:
                results.append(doc)
        return results

    async def do_get_filter_options(self, identity_helper: IdentityHelper) -> dict:
        """Fetch available filter options (correspondents, document types, tags) for all identities.

        Args:
            identity_helper: Resolved user identities for filtering.

        Returns:
            dict with keys 'correspondents', 'document_types', 'tags' — each a deduplicated list[str].
        """
        result: dict[str, list[str]] = {"correspondents": [], "document_types": [], "tags": []}
        for identity in identity_helper.get_identities():
            cache_data = await self._cache_helper.get_data(identity.dms_engine, identity.owner_id)
            for key in ("correspondents", "document_types", "tags"):
                existing = result[key]
                for value in (getattr(cache_data, key) or []):
                    if value not in existing:
                        existing.append(value)
        return result
        
    def get_document_url_by_id(self, dms_engine:str, doc_id:str) -> str|None:
        """
        Fetches the document view URL for a given document by identifying the correct DMS client based on the dms_engine and retrieving the URL using the doc_id.

        Args:
            dms_engine: The DMS engine name (e.g. "paperless") to identify the correct DMS client.
            doc_id: The document ID within the DMS for which to retrieve the view URL.

        Returns:
            str|None: The URL to view the document in the DMS, or None if the URL cannot be retrieved.
        """
        # find correct dms
        dms_client = next((c for c in self._dms_clients if c.get_engine_name().lower() == dms_engine.lower()), None)
        if not dms_client:
            return None
        try:
            url = dms_client.get_document_view_url(doc_id)
            return url.strip() if url and url.strip() else None
        except Exception as e:
            return None

    ##########################################
    ############# HELPERS ####################
    ##########################################

    async def _execute_search(
        self,
        query_vector: list[float],
        filters: dict,
    ) -> list[PointHighDetails]:
        """Run vector similarity search across all RAG clients and collect results.

        Args:
            query_vector: Embedded query vector.
            filters: Pre-built RAG filter object e.g. {"must": [...]}.
            limit: Maximum number of results to return per RAG client.

        Returns:
            Merged list of PointHighDetails objects from all RAG clients.
        """
        search_tasks = [
            rag_client.do_search_points(
                vector=query_vector,
                filters=filters,
                include_fields=True,
                with_vector=False,
            )
            for rag_client in self._rag_clients
        ]
        search_responses = await asyncio.gather(*search_tasks, return_exceptions=True)

        results: list[PointHighDetails] = []
        for idx, points in enumerate(search_responses):
            if isinstance(points, Exception):
                self.logging.warning(
                    "SearchService._execute_search: RAG client [%d] failed: %s",
                    idx, points,
                )
                continue
            for point in points:
                point.score = point.score or 0.0  # ensure score is a float for sorting later
                results.append(point)
        return results
    
    def _merge_points(self, points: list[PointHighDetails]) -> list[PointHighDetails]:
        """
        Merge RAG chunks from the same document into one entry, concatenating content.
        Concatenates chunk_text of points with the same dms_doc_id, sorted by score descending (highest score first).

        Args:
            points: List of PointHighDetails objects to merge.

        Returns:
            List of PointHighDetails with chunks from the same document merged into single entries.
        """
        # group points by document ID
        docs: dict[str, list[PointHighDetails]] = {}
        for point in points:
            key = point.dms_doc_id or ""
            if key not in docs:
                docs[key] = []
            docs[key].append(point)
        # for each document, sort chunks and concatenate their text
        merged: list[PointHighDetails] = []
        for chunks in docs.values():
            # sort chunks by score descending, treating None as 0.0
            chunks.sort(key=lambda p: p.score or 0.0, reverse=True)
            base = chunks[0]
            if len(chunks) > 1:
                extra = "\n\n".join(c.chunk_text for c in chunks[1:] if c.chunk_text)
                base.chunk_text = (base.chunk_text or "") + "\n\n" + extra
            merged.append(base)
        return merged

