from shared.helper.HelperConfig import HelperConfig
from shared.clients.rag.RAGClientInterface import RAGClientInterface
from shared.clients.rag.models.Point import PointsListResponse, PointHighDetails, PointUpsert, PointHighDetailsRequest
from shared.models.config import EnvConfig


class RAGClientQdrant(RAGClientInterface):
    def __init__(self, helper_config: HelperConfig):
        super().__init__(helper_config=helper_config)
        self._base_url = self.get_config_val("BASE_URL", default=None, val_type="string")
        self._api_key = self.get_config_val("API_KEY", default="", val_type="string")
        self._collection_name = self.get_config_val("COLLECTION", default=None, val_type="string")
        self._top_k = self.get_config_val("TOP_K", default=10, val_type="number")

    ##########################################
    ################ GETTER ##################
    ##########################################

    ################ GENERAL ##################
    def _get_engine_name(self) -> str:
        return "Qdrant"

    ################ CONFIG ##################
    def _get_required_config(self) -> list[EnvConfig]:
        return [
            EnvConfig(env_key="BASE_URL", val_type="string", default=None),
            EnvConfig(env_key="API_KEY", val_type="string", default=""),
            EnvConfig(env_key="COLLECTION", val_type="string", default=None),
            EnvConfig(env_key="TOP_K", val_type="number", default=10)
        ]

    ################ AUTH ##################
    def _get_auth_header(self) -> dict:
        if self._api_key:
            return {"api-key": f"{self._api_key}"}
        else:
            return {}

    ################ ENDPOINTS ##################
    def _get_base_url(self) -> str:
        return self._base_url

    def _get_endpoint_healthcheck(self) -> str:
        return "/healthz"

    def _get_endpoint_scroll(self) -> str:
        return f"/collections/{self._collection_name}/points/scroll"

    def _get_endpoint_points(self) -> str:
        return f"/collections/{self._collection_name}/points"

    def _get_endpoint_delete_points(self) -> str:
        return f"/collections/{self._collection_name}/points/delete"

    def _get_endpoint_check_collection_existence(self) -> str:
        return f"/collections/{self._collection_name}/exists"

    def _get_endpoint_create_collection(self) -> str:
        return f"/collections/{self._collection_name}"

    def _get_endpoint_count(self) -> str:
        return f"/collections/{self._collection_name}/points/count"

    def _get_endpoint_search(self) -> str:
        return f"/collections/{self._collection_name}/points/search"
    
    
    ##########################################
    ########### RESPONSE PARSER ##############
    ##########################################

    ############### LIST RESPONSES ###############
    def _parse_endpoint_points(self, response: dict, requested_page_size:int, total_points:int, current_page:int) -> PointsListResponse:
        meta = self._parse_listing_meta(response, total_points, current_page)
        
        # since paperless sends details in the list, we can parse them directly into PointDetails objects
        points = []
        results = response.get("result",{})
        for item in results.get("points", []):
            point = self._parse_endpoint_point(item)
            points.append(point)

        # return answer
        pageLen = len(points) if not requested_page_size else requested_page_size
        return PointsListResponse(
            engine=self._get_engine_name(),
            points=points,
            currentPage=meta["current_page"],
            nextPage=meta["next_page"],
            nextPageId=meta["next_page_id"],
            previousPage=meta["previous_page"],
            overallCount=meta["overall_results_count"],
            pageLength=pageLen,
            lastPage= meta["overall_results_count"] // pageLen + (1 if meta["overall_results_count"] % pageLen > 0 else 0) if meta["overall_results_count"] and pageLen else None
        )

    def _parse_listing_meta(self, listing_response:dict, total_points:int, current_page:int) -> dict:
        """
        Parse the metadata from a listing response, including pagination details.

        Args:
            listing_response (dict): The raw response from the DMS listing endpoint.
            total_points (int): The total number of points in the collection.
            current_page (int): The current page number being requested.

        Returns:
            dict: A dictionary containing pagination metadata such as current_page, next_page, previous_page, and overall_results_count.
        """
        result = listing_response.get("result")
        next_page_offset = result.get("next_page_offset", None)
        overall_results_count = total_points
        next_page: int | None = None
        if next_page_offset:
            next_page = current_page + 1
        previous_page = current_page - 1 if current_page > 1 else None

        return {
            "current_page": current_page,
            "next_page": next_page,
            "next_page_id": next_page_offset,
            "previous_page": previous_page,
            "overall_results_count": overall_results_count
        }    
    
    ############### GET RESPONSES ###############
    def _parse_endpoint_points_search(self, response: dict) -> list[PointHighDetails]:
        results = response.get("result", [])
        final_points = []
        for point_dict in results:
            point = self._parse_endpoint_point(point_dict)
            # add score and vector if present in the response (they are not included in the payload by default)
            point.score = point_dict.get("score", None)
            point.vector = point_dict.get("vector", None)
            final_points.append(point)            
        return final_points

    def _parse_endpoint_point(self, response: dict) -> PointHighDetails:
        payload_details = response.get("payload", {})
        return PointHighDetails(
                #base
                engine=self._get_engine_name(),
                id=str(response.get("id")),

                #details
                dms_doc_id=payload_details.get("dms_doc_id",None),
                content_hash=payload_details.get("content_hash",None),
                dms_engine=payload_details.get("dms_engine",None),

                #high details
                chunk_index=payload_details.get("chunk_index", None),
                title=payload_details.get("title",None),
                owner_id=payload_details.get("owner_id",None),
                created=payload_details.get("created", None),
                chunk_text=payload_details.get("chunk_text", None),
                label_ids=payload_details.get("label_ids", []),
                label_names=payload_details.get("label_names", []),
                category_id=payload_details.get("category_id", None),
                category_name=payload_details.get("category_name", None),
                type_id=payload_details.get("type_id", None),
                type_name=payload_details.get("type_name", None),
                owner_username=payload_details.get("owner_username", None),
                # custom_fields is stored as a plain dict in the Qdrant payload; default to empty
                # dict so callers never have to guard against None
                custom_fields=payload_details.get("custom_fields", {}),
                score=response.get("score", None),
                vector=response.get("vector", None)
            )
    
    def _parse_endpoint_points_count(self, response: dict) -> int:
        return response.get("result", {}).get("count", 0)
    
    ############### UPDATE RESPONSES ###############
    def _parse_endpoint_points_upsert(self, response: dict) -> bool:
        return response.get("status", "failed").lower() == "ok"
    
    ############### DELETE RESPONSES ###############
    def _parse_endpoint_points_delete(self, response: dict) -> bool:
        return response.get("status", "failed").lower() == "ok"

    ##########################################
    ############### PAYLOADS #################
    ##########################################

    def _get_scroll_payload(
        self, 
        filters: list[dict], 
        include_fields: bool | list | dict, 
        with_vector: bool | list, 
        limit: int | None = None, 
        next_page_id: str | None = None) -> dict:
        payload = {
            "filter": {"must": filters},
            "limit": limit,
            "with_payload": include_fields,
            "with_vector": with_vector,
        }
        if next_page_id is not None:
            payload["offset"] = next_page_id
        return payload
    
    def _get_search_payload(
        self,
        filters: dict,
        include_fields: bool | list | dict,
        with_vector: bool,
        vector: list[float]
    ) -> dict:
        return {
            "vector": vector,
            "filter": filters,
            "limit": self._top_k,
            "with_payload": include_fields,
            "with_vector": with_vector,
        }

    def get_count_payload(self, filters: list[dict]) -> dict:
        return {"filter": {"must": filters}, "exact": True}

    def get_delete_payload(self, filter: dict) -> dict:
        return {"filter": filter}
    
    def get_upsert_payload(self, points: list[PointUpsert]) -> dict:
        serialised = []
        for point in points:
            if not point.payload.owner_id:
                raise ValueError(
                    "Security invariant violated: UpsertPoint id=%s has no owner_id." % point.id
                )
            serialised.append({
                "id": point.id,
                "vector": point.vector,
                "payload": point.payload.model_dump(),
            })
        return {"points": serialised}