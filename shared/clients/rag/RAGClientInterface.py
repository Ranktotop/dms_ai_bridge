from abc import abstractmethod
import re

import httpx
from shared.clients.rag.models.Point import PointUpsert, PointHighDetails, PointsListResponse
from shared.clients.ClientInterface import ClientInterface
import json

from shared.helper.HelperConfig import HelperConfig


class RAGClientInterface(ClientInterface):
    def __init__(self, helper_config: HelperConfig):
        super().__init__(helper_config=helper_config)

    ##########################################
    ################ GETTER ##################
    ##########################################

    ################ GENERAL ##################
    def _get_client_type(self) -> str:
        """
        Returns the type of the client. E.g. "rag"
        """
        return "rag"

    ################ ENDPOINTS ##################
    @abstractmethod
    def _get_endpoint_scroll(self) -> str:
        """
        Returns the endpoint path for scroll requests.

        Returns:
            str: The endpoint path for scroll requests (e.g. "/scroll")

        Raises:
            NotImplementedError: If the method is not implemented in a subclass.
        """
        pass

    @abstractmethod
    def _get_endpoint_points(self) -> str:
        """
        Returns the endpoint path for points upsert requests.

        Returns:
            str: The endpoint path for points requests (e.g. "/points")

        Raises:
            NotImplementedError: If the method is not implemented in a subclass.
        """
        pass

    @abstractmethod
    def _get_endpoint_delete_points(self) -> str:
        """
        Returns the endpoint path for deleting points by filter.

        Returns:
            str: The endpoint path (e.g. "/collections/my_col/points/delete")

        Raises:
            NotImplementedError: If the method is not implemented in a subclass.
        """
        pass

    @abstractmethod
    def _get_endpoint_check_collection_existence(self) -> str:
        """
        Returns the endpoint path for collection existence check requests.

        Returns:
            str: The endpoint path for collection existence check requests (e.g. "/existence_check")

        Raises:
            NotImplementedError: If the method is not implemented in a subclass.
        """
        pass

    @abstractmethod
    def _get_endpoint_create_collection(self) -> str:
        """
        Returns the endpoint path for create collection requests.

        Returns:
            str: The endpoint path for create collection requests (e.g. "/create_collection")

        Raises:
            NotImplementedError: If the method is not implemented in a subclass.
        """
        pass

    @abstractmethod
    def _get_endpoint_count(self) -> str:
        """Returns the endpoint path for counting points matching a filter.

        Returns:
            str: The endpoint path (e.g. "/collections/my_col/points/count")

        Raises:
            NotImplementedError: If the method is not implemented in a subclass.
        """
        pass

    @abstractmethod
    def _get_endpoint_search(self) -> str:
        """Returns the endpoint path for vector similarity search requests.

        Returns:
            str: The endpoint path (e.g. "/collections/my_col/points/search")

        Raises:
            NotImplementedError: If the method is not implemented in a subclass.
        """
        pass

    ##########################################
    ############### PAYLOADS #################
    ##########################################

    def get_scroll_payload(self, filters: list[dict], include_fields: bool | list | dict, with_vector: bool | list, limit: int | None = None, next_page_id: str | None = None)->dict:
        """
        Returns the payload template for scroll requests to the RAG backend.

        Args:
            filters (list[dict]): The filters to apply to the scroll request.
            include_fields (bool | list | dict): Which fields to include in the scroll request.
            with_vector (bool | list): Whether to include the vector in the scroll request, or which vector fields to include.
            limit (int | None): The maximum number of results to return.
            next_page_id (str | None): Pagination cursor returned by the previous scroll page.
                                        None means start from the beginning.

        Returns:
            dict: The payload for the scroll request.

        Raises:
            NotImplementedError: If the method is not implemented in a subclass.
        """
        # make sure some fields are always included for correct parsing of points
        required_fields = ["dms_doc_id", "dms_engine", "content_hash"]
        if isinstance(include_fields, list):
            #copy list to avoid modifying original
            include_fields = include_fields.copy()
            for field in required_fields:
                if field not in include_fields:
                    include_fields.append(field)
        elif isinstance(include_fields, dict) and not include_fields.get("include_all", False):
            # copy dict to avoid modifying original
            include_fields = include_fields.copy()
            raise NotImplementedError("include_fields as dict with field-level control is not implemented yet. Please use a list of fields or set include_all to True.")
        else:
            # if include_fields is bool there are already all fields included
            pass
        return self._get_scroll_payload(filters, include_fields, with_vector, limit, next_page_id)


    @abstractmethod
    def _get_scroll_payload(self, filters: list[dict], include_fields: bool | list | dict, with_vector: bool | list, limit: int | None = None, next_page_id: str | None = None) -> dict:
        """
        Returns the payload template for scroll requests to the RAG backend.

        Args:
            filters (list[dict]): The filters to apply to the scroll request.
            include_fields (bool | list | dict): Which fields to include in the scroll request.
            with_vector (bool | list): Whether to include the vector in the scroll request, or which vector fields to include.
            limit (int | None): The maximum number of results to return.
            next_page_id (str | None): Pagination cursor returned by the previous scroll page.
                                        None means start from the beginning.

        Returns:
            dict: The payload for the scroll request.

        Raises:
            NotImplementedError: If the method is not implemented in a subclass.
        """
        pass

    def get_search_payload(
        self,
        filters: dict,
        include_fields: bool | list | dict,
        with_vector: bool,
        vector: list[float]
    ) -> dict:
        """Builds the backend-specific request payload for a vector similarity search.

        Args:
            filters: Full filter object e.g. {"must": [...]}.
            include_fields: Whether to include the payload in results.
            with_vector: Whether to include the stored vector in results.
            vector: Query embedding vector.

        Returns:
            dict: The payload for the search request.

        Raises:
            NotImplementedError: If the method is not implemented in a subclass.
        """
        # make sure some fields are always included for correct parsing of points
        required_fields = ["dms_doc_id", "dms_engine", "content_hash"]
        if isinstance(include_fields, list):
            #copy list to avoid modifying original
            include_fields = include_fields.copy()
            for field in required_fields:
                if field not in include_fields:
                    include_fields.append(field)
        elif isinstance(include_fields, dict) and not include_fields.get("include_all", False):
            # copy dict to avoid modifying original
            include_fields = include_fields.copy()
            raise NotImplementedError("include_fields as dict with field-level control is not implemented yet. Please use a list of fields or set include_all to True.")
        else:
            # if include_fields is bool there are already all fields included
            pass
        return self._get_search_payload(filters=filters, include_fields=include_fields, with_vector=with_vector, vector=vector)

    @abstractmethod
    def _get_search_payload(
        self,
        filters: dict,
        include_fields: bool | list | dict,
        with_vector: bool,
        vector: list[float]
    ) -> dict:
        """Builds the backend-specific request payload for a vector similarity search.

        Args:
            filters: Full filter object e.g. {"must": [...]}.
            include_fields: Whether to include the payload in results.
            with_vector: Whether to include the stored vector in results.
            vector: Query embedding vector.

        Returns:
            dict: The payload for the search request.

        Raises:
            NotImplementedError: If the method is not implemented in a subclass.
        """
        pass

    @abstractmethod
    def get_count_payload(self, filters: list[dict]) -> dict:
        """Builds the backend-specific request payload for a point count.

        Args:
            filters (list[dict]): Filter conditions to apply before counting.

        Returns:
            dict: The payload for the count request.

        Raises:
            NotImplementedError: If the method is not implemented in a subclass.
        """
        pass

    @abstractmethod
    def get_delete_payload(self, filter: dict) -> dict:
        """
        Builds the backend-specific request payload for a filter-based delete.

        Args:
            filter (dict): The filter that identifies which points to delete.

        Returns:
            dict: The payload for the delete request.

        Raises:
            NotImplementedError: If the method is not implemented in a subclass.
        """
        pass 

    @abstractmethod
    def get_upsert_payload(self, points: list[PointUpsert]) -> dict:
        """Build the backend-specific JSON body for a batch upsert.

        Args:
            points (list[PointUpsert]): Typed points to upsert.

        Returns:
            dict: The request body for the upsert endpoint.

        Raises:
            NotImplementedError: If the method is not implemented in a subclass.
        """
        pass

    ##########################################
    ############### REQUESTS #################
    ##########################################

    async def do_existence_check(self) -> bool:
        """Check if a collection exists in the rag backend.

        Returns:
            bool: True if the collection exists, False otherwise.
        """
        resp = await self.do_request(method="GET", endpoint=self._get_endpoint_check_collection_existence())
        return resp.json().get("result", {}).get("exists")

    async def do_create_collection(self, vector_size: int = 768, distance: str = "Cosine") -> httpx.Response:
        """Create a collection in the rag backend.

        Args:
            vector_size (int): The size of the vectors in the collection.
            distance (str): The distance metric for the vectors.

        Returns:
            httpx.Response: The response from the create collection request.
        """
        return await self.do_request(
            method="PUT",
            json={
                "vectors": {
                    "size": vector_size,
                    "distance": distance}},
            endpoint=self._get_endpoint_create_collection())

    async def do_upsert_points(self, points: list[PointUpsert]) -> bool:
        """Upsert points into the rag backend collection.
        Inserts new points or replaces existing ones if a point with the same ID already exists.

        Args:
            points (list[PointUpsert]): The typed list of points to upsert.

        Returns:
            bool: True if the upsert was successful, False otherwise.
        """
        resp = await self.do_request(
            method="PUT",
            content=json.dumps(self.get_upsert_payload(points)),
            endpoint=self._get_endpoint_points(),
            additional_headers={"Content-Type": "application/json"})
        return self._parse_endpoint_points_upsert(resp.json())

    async def do_delete_points_by_filter(self, filter: dict) -> bool:
        """Deletes all points matching the given filter from the RAG backend.
        Used when a DMS document is updated (delete old chunks) or deleted entirely.

        Args:
            filter (dict): The filter that identifies which points to delete.
                           Must always include owner_id to enforce access isolation.

        Returns:
            bool: True if the delete was successful, False otherwise.
        """
        resp = await self.do_request(
            method="POST",
            content=json.dumps(self.get_delete_payload(filter)),
            endpoint=self._get_endpoint_delete_points(),
            additional_headers={"Content-Type": "application/json"},
            raise_on_error=True,
        )    
        return self._parse_endpoint_points_delete(resp.json())

    async def do_count(self, filters: list[dict]) -> int:
        """Count the total number of points matching the given filters.

        Args:
            filters (list[dict]): Filter conditions for the count request.

        Returns:
            int: Total number of matching points.
        """
        resp = await self.do_request(
            method="POST",
            content=json.dumps(self.get_count_payload(filters)),
            endpoint=self._get_endpoint_count(),
            additional_headers={"Content-Type": "application/json"},
            raise_on_error=True,
        )
        return self._parse_endpoint_points_count(resp.json())
    
    async def do_search_points(
        self,
        vector: list[float],
        filters: dict,
        include_fields: bool | list | dict = True,
        with_vector: bool = False,
    ) -> list[PointHighDetails]:
        """Search for points by vector similarity + optional filters.

        Uses the backend's native ANN search endpoint (e.g. Qdrant /points/search).
        Results are ranked by similarity score descending.

        Args:
            vector: Query embedding vector.
            filters: Full filter object e.g. {"must": [...]}.
            include_fields: Whether to include the payload in results.
            with_vector: Whether to include the stored vector in results.

        Returns:
            list[PointHighDetails]: List of points ranked by score.
        """
        resp = await self.do_request(
                method="POST",
                content=json.dumps(self.get_search_payload(filters=filters, include_fields=include_fields, with_vector=with_vector, vector=vector)),
                endpoint=self._get_endpoint_search(),
                additional_headers={"Content-Type": "application/json"},
                raise_on_error=True,
            )
        points_search_response = self._parse_endpoint_points_search(resp.json())
        #remove all points without content
        filtered_points = [p for p in points_search_response if p.chunk_text.strip()]
        return filtered_points

    async def do_fetch_points(self, filters: dict, include_fields: bool | list | dict, with_vector: bool | list) -> list[PointHighDetails]:
        """Fetch ALL points matching the filter, paginating automatically.

        Analogous to do_fetch_documents() in DMSClientInterface — runs a loop
        driven by next_page_offset until the backend signals there are no more pages.

        Args:
            filters (dict): The filters to apply to the scroll request.
            include_fields (bool | list | dict): Which fields to include in the results.
            with_vector (bool | list): Whether to include the vector in each result point.

        Returns:
            list[PointHighDetails]: All matching points collected across all pages.
        """
        points = []
        page = 1
        page_size = 1000
        total_points = await self.do_count(filters)

        next_page_id: str | None = None
        
        while True:
            resp = await self.do_request(
                method="POST",
                content=json.dumps(self.get_scroll_payload(filters, include_fields, with_vector, page_size, next_page_id)),
                endpoint=self._get_endpoint_scroll(),
                additional_headers={"Content-Type": "application/json"},
                raise_on_error=True,
            )
            points_list_response = self._parse_endpoint_points(resp.json(), requested_page_size=page_size, total_points=total_points, current_page=page)
            points.extend(points_list_response.points)
            self.logging.debug("Fetched points page %d of %d from %s, total points so far: %d of %d", page, points_list_response.lastPage, self._get_engine_name(), len(points), points_list_response.overallCount)
            next_page_id = points_list_response.nextPageId
            page = points_list_response.nextPage
            if not next_page_id:
                break            
        return points
    
    ##########################################
    ########### RESPONSE PARSER ##############
    ##########################################

    ############### LIST RESPONSES ###############
    @abstractmethod
    def _parse_endpoint_points(self, response: dict, requested_page_size:int, total_points:int, current_page:int) -> PointsListResponse:
        """
        Parses the response from the point listing endpoint and returns a list of points with their metadata.

        Args:
            response (dict): The raw response from the point listing endpoint.
            requested_page_size (int): The page size that was requested for this point listing. This is used to calculate the last page number in the pagination info.
            total_points (int): The total number of points in the collection.
            current_page (int): The current page number being requested.
        Returns:
            PointsListResponse: The parsed response containing a list of points and pagination information.
        """
        pass

    ############### GET RESPONSES ###############
    @abstractmethod
    def _parse_endpoint_points_search(self, response: dict) -> list[PointHighDetails]:
        """
        Parses the response from the point search endpoint and returns a list of points with their metadata.

        Args:
            response (dict): The raw response from the point search endpoint.
        Returns:
            list[PointHighDetails]: The parsed response containing a list of points with their metadata.
        """

    @abstractmethod
    def _parse_endpoint_points_count(self, response: dict) -> int:
        """
        Parses the response from the point count endpoint and returns the total number of matching points.

        Args:
            response (dict): The raw response from the point count endpoint.
        Returns:            
            int: The total number of matching points.
        """
        pass

    ############### UPDATE RESPONSES ###############
    @abstractmethod
    def _parse_endpoint_points_upsert(self, response: dict) -> bool:
        """
        Parses the response from the point upsert endpoint and returns whether the upsert was successful.

        Args:
            response (dict): The raw response from the point upsert endpoint.
        Returns:
            bool: True if the upsert was successful, False otherwise.
        """
        pass

    ############### DELETE RESPONSES ###############
    @abstractmethod
    def _parse_endpoint_points_delete(self, response: dict) -> bool:
        """
        Parses the response from the point delete endpoint and returns whether the delete was successful.

        Args:
            response (dict): The raw response from the point delete endpoint.
        Returns:
            bool: True if the delete was successful, False otherwise.
        """
        pass

    ##########################################
    ############# HELPERS ####################
    ##########################################

    def get_chunk_size(self) -> int:
        """Return the maximum character length per text chunk."""
        return 1000

    def get_chunk_overlap(self) -> int:
        """Return the number of characters to overlap between consecutive chunks."""
        return 100

    def split_text(self, text: str) -> list[str]:
        """Split document text into semantically coherent, overlapping chunks.

        Uses a recursive strategy that tries structural separators in order
        (Markdown headings → paragraphs → lines → sentences → words → characters).
        Small pieces are merged back up to get_chunk_size() before overlap is added.

        Args:
            text (str): Full document text.

        Returns:
            list[str]: Ordered list of text chunks.
        """
        if not text:
            return []
        pieces = self._split_text_recursive(text.strip(), 0)
        merged = self._merge_chunks(pieces)
        return self._add_overlap(merged)

    def _split_text_recursive(self, text: str, sep_idx: int) -> list[str]:
        """Recursively split text using separators starting at sep_idx.

        Returns the text unchanged if it already fits within get_chunk_size().
        Falls back to a hard character split if no separator produces multiple parts.

        Args:
            text (str): Text segment to split.
            sep_idx (int): Index into separators to start from.

        Returns:
            list[str]: List of text pieces, each at most get_chunk_size() characters.
        """
        # Separators tried in order during recursive splitting.
        # Each pattern splits at a semantic boundary; finer-grained fallbacks follow.
        separators: list[re.Pattern[str]] = [
            re.compile(r"(?=\n#{1,6} )"),  # Markdown headings (zero-width — keeps \n# with next chunk)
            re.compile(r"\n\n+"),          # Blank lines / paragraphs
            re.compile(r"\n"),             # Single line breaks
            re.compile(r"(?<=[.!?]) +"),   # Sentence boundaries (period/!/?  stays with left chunk)
            re.compile(r" +"),             # Word boundaries
        ]
        chunk_size = self.get_chunk_size()
        if len(text) <= chunk_size:
            return [text]
        for i in range(sep_idx, len(separators)):
            parts = [p.strip() for p in separators[i].split(text) if p.strip()]
            if len(parts) > 1:
                result: list[str] = []
                for part in parts:
                    if len(part) <= chunk_size:
                        result.append(part)
                    else:
                        result.extend(self._split_text_recursive(part, i + 1))
                return result
        # Hard character fallback — no separator produced a split
        return [text[j: j + chunk_size] for j in range(0, len(text), chunk_size)]

    def _merge_chunks(self, pieces: list[str]) -> list[str]:
        """Merge consecutive small pieces into chunks up to get_chunk_size().

        Pieces are joined with a newline. If adding the next piece would exceed
        get_chunk_size() the current buffer is flushed as a new chunk first.

        Args:
            pieces (list[str]): Atomic text pieces produced by _split_text_recursive.

        Returns:
            list[str]: Merged chunks, each at most get_chunk_size() characters.
        """
        chunk_size = self.get_chunk_size()
        chunks: list[str] = []
        buf = ""
        for piece in pieces:
            candidate = (buf + "\n" + piece) if buf else piece
            if len(candidate) <= chunk_size:
                buf = candidate
            else:
                if buf:
                    chunks.append(buf)
                buf = piece
        if buf:
            chunks.append(buf)
        return chunks

    def _add_overlap(self, chunks: list[str]) -> list[str]:
        """Prepend the tail of the previous chunk to each subsequent chunk.

        Overlap improves retrieval at chunk boundaries by giving the embedding
        model context that spans two adjacent chunks.

        Args:
            chunks (list[str]): Merged chunks without overlap.

        Returns:
            list[str]: Chunks with get_chunk_overlap() characters of leading overlap
                       (all chunks except the first).
        """
        chunk_overlap = self.get_chunk_overlap()
        if len(chunks) <= 1 or not chunk_overlap:
            return chunks
        result = [chunks[0]]
        for i in range(1, len(chunks)):
            tail = chunks[i - 1][-chunk_overlap:]
            result.append(tail + "\n" + chunks[i])
        return result

    async def do_reconstruct_document_content(self, dms_engine: str, dms_doc_id: str, owner_id: str) -> str | None:
        """Reconstruct the original document text from its RAG chunks.

        Fetches all points for the given document, sorts them by chunk_index,
        and strips the overlap prefix from each chunk (except the first) to
        recover the original merged text segments. The segments are then joined
        with a newline to form the reconstructed document content.

        The reconstruction is exact with respect to the chunking pipeline:
        for each chunk i > 0 the stored text is
            original[i-1][-chunk_overlap:] + "\\n" + original[i]
        so stripping that known prefix recovers original[i] iteratively.

        Args:
            dms_engine (str): DMS engine identifier (e.g. "paperless").
            dms_doc_id (str): Document ID within that DMS engine.
            owner_id (str): Owner ID as string — enforced for access isolation.
        Returns:
            str: Reconstructed document content, or None if no points were found.
        """
        points = await self.do_fetch_points(
            filters=[
                {"key": "dms_engine", "match": {"value": dms_engine}},
                {"key": "dms_doc_id", "match": {"value": dms_doc_id}},
                {"key": "owner_id",   "match": {"value": owner_id}},
            ],
            include_fields=["chunk_index", "chunk_text"],
            with_vector=False,
        )
        if not points:
            return None

        points.sort(key=lambda p: p.chunk_index if p.chunk_index is not None else 0)

        chunk_overlap = self.get_chunk_overlap()
        originals: list[str] = []
        for i, point in enumerate(points):
            stored = point.chunk_text or ""
            if i == 0 or not chunk_overlap:
                originals.append(stored)
            else:
                # The stored text starts with original[i-1][-chunk_overlap:] + "\n".
                # Strip that prefix to recover the original segment.
                overlap_prefix_len = len(originals[i - 1][-chunk_overlap:]) + 1  # +1 for "\n"
                originals.append(stored[overlap_prefix_len:])

        return "\n".join(originals)