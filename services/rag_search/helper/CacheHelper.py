
from shared.clients.cache.CacheClientInterface import KEY_FILTER_OPTIONS
from shared.clients.cache.CacheClientInterface import CacheClientInterface
from shared.clients.rag.RAGClientInterface import RAGClientInterface
from shared.helper.HelperConfig import HelperConfig
import asyncio

from dataclasses import dataclass, field

@dataclass
class RagResponse:
    dms_engine: str
    owner_id: str
    cache_key:str
    correspondents: list[str] = field(default_factory=list)
    document_types: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    # field_name → sorted list of distinct values across all documents for this owner
    custom_fields: dict[str, list[str]] = field(default_factory=dict)

class CacheHelper:
    def __init__(self, cache_client: CacheClientInterface, rag_clients: list[RAGClientInterface], config: HelperConfig) -> None:
        self._cache_client = cache_client
        self._config = config
        self._rag_clients = rag_clients
        self.logging = config.get_logger()

    ##########################################
    ################# GETTER #################
    ##########################################

    async def get_data(self, dms_engine: str, owner_id: str, force_live: bool = False) -> RagResponse:
        data:RagResponse|None = None
        source = "unknown"
        # fetch from cache primary if not forced live
        cache_result = await self._fetch_from_cache(dms_engine, owner_id) if not force_live else None
        if cache_result is not None:
            source = "cached"
            data = cache_result
        # fetch from rags secondary
        else:
            source = "live"
            data = await self._fetch_from_rags(dms_engine, owner_id)

        #error gate
        if data is None:
            raise Exception(
                "Failed to fetch data for engine %s and owner_id %s from both cache and RAG backends."
                % (dms_engine, owner_id)
            )
        #convert RagResponse data to dict (with copy) and log the count of each filter value for better readability
        dict_data = data.__dict__.copy()
        dict_data["correspondents"] = len(dict_data["correspondents"])
        dict_data["document_types"] = len(dict_data["document_types"])
        dict_data["tags"] = len(dict_data["tags"])
        self.logging.debug("Fetched %s data for engine %s and owner_id %s:\n%s", source, dms_engine, owner_id, dict_data)

        # return the original data object
        return data

    ##########################################
    ################# CACHE ##################
    ##########################################

    def _create_cache_key(self, *values: str) -> str:
        """
        Returns a cache key for the provided values.

        Args:
            values: Variable length argument list of strings to be included in the cache key after the namespace.

        Returns:
            str: Cache key for filter options corresponding to the provided values.
        """
        #dynamically create cache keys based on the number of values provided
        base_key = KEY_FILTER_OPTIONS
        for value in values:
            base_key += ":%s" % value
        return base_key

    async def _fetch_from_cache(self, dms_engine: str, owner_id: str) -> RagResponse|None:
        """
        Fetches a value from the cache based on the provided filter values.

        Args:
            dms_engine: The DMS engine name. E.g. paperless, filestash, etc.
            owner_id: The owner ID.
        Returns:
            RagResponse|None: The value corresponding to the provided filter values fetched from cache. Returns None if cache miss occurs.
        """
        # make sure engine is lc
        dms_engine = dms_engine.lower()
        key = self._create_cache_key(dms_engine, owner_id)
        cache_result = await self._cache_client.do_get_json(key)
        if cache_result is None:
            return None
        result = RagResponse(
            dms_engine=dms_engine,
            owner_id=owner_id,
            cache_key=key,
            correspondents=cache_result.get("correspondents", []),
            document_types=cache_result.get("document_types", []),
            tags=cache_result.get("tags", []),
            # custom_fields was added after the initial cache schema — default to {} for
            # cache entries written before this field was introduced
            custom_fields=cache_result.get("custom_fields", {}))
        self.logging.debug("Fetching from cache with key: %s, result: %s", key, result)
        return result

    async def _fetch_from_rags(self, dms_engine: str, owner_id: str) -> RagResponse:
        """
        Fetches all documents for given dms_engine and owner_id from RAG backends, extracts distinct filter values, and returns them as a merged RagResponse object.
        The result is also written to cache for future requests.

        Args:
            dms_engine: The DMS engine name. E.g. paperless, filestash, etc.
            owner_id: The owner ID.

        Returns:
            RagResponse: The values corresponding to the provided filter values fetched from RAG backend.
        """
        # make sure engine is lc
        dms_engine = dms_engine.lower()

        #define the scroll task for rag client
        self.logging.debug("Fetching data for engine %s and owner_id %s from RAG backend.", dms_engine, owner_id)
        #define the scroll tasks as coroutines for all rag clients
        scroll_tasks = [
            rag_client.do_fetch_points(
                filters=[
                    {"key": "dms_engine", "match": {"value": dms_engine}},
                    {"key": "owner_id", "match": {"value": owner_id}},
                ],
                include_fields=["category_name", "type_name", "label_names", "custom_fields"],
                with_vector=False,
            )
            for rag_client in self._rag_clients
        ]
        #execute all scroll tasks in parallel and gather results
        scroll_responses = await asyncio.gather(*scroll_tasks, return_exceptions=True)

        #Prepare empty response object to collect results from all rag clients for this dms engine and owner_id.
        result = RagResponse(
            dms_engine=dms_engine,
            owner_id=owner_id,
            cache_key=self._create_cache_key(dms_engine, owner_id),
            correspondents=set(),
            document_types=set(),
            tags=set())
        # separate accumulator for custom fields — must be dict[str, set[str]] during
        # collection because sets deduplicate values; converted to sorted lists at the end
        custom_field_sets: dict[str, set[str]] = {}

        #iterate results from all rag clients
        has_errors = False
        for idx, points in enumerate(scroll_responses):

            #get the corresponding rag client
            rag_client = self._rag_clients[idx]

            #if result is exception, log warning and continue with next result
            if isinstance(points, Exception):
                self.logging.warning(
                    "Error occured while fetching data from RAG client [%s]: %s",
                    rag_client.get_engine_name(), points, color="yellow"
                )
                has_errors = True
                continue

            for point in points:
                if point.category_name:
                    result.correspondents.add(point.category_name)
                if point.type_name:
                    result.document_types.add(point.type_name)
                for label in point.label_names:
                    if label:
                        result.tags.add(label)
                # accumulate distinct values per custom field name for filter options
                for field_name, value in (getattr(point, "custom_fields", None) or {}).items():
                    if field_name and value:
                        custom_field_sets.setdefault(field_name, set()).add(value)

        #sort the sets and convert to lists for better readability
        result.correspondents = sorted(result.correspondents)
        result.document_types = sorted(result.document_types)
        result.tags = sorted(result.tags)
        result.custom_fields = {k: sorted(v) for k, v in custom_field_sets.items()}

        #upsert to cache if not error occured during fetching from rags
        if not has_errors:
            try:
                cache_data = {
                    "correspondents": result.correspondents,
                    "document_types": result.document_types,
                    "tags": result.tags,
                    "custom_fields": result.custom_fields}
                await self._cache_client.do_set_json(result.cache_key, cache_data)
            except Exception as e:
                self.logging.warning(
                    "Error occured while writing rag data to cache with key %s: %s",
                    result.cache_key, e, color="yellow"
                )

        return result
