import asyncio

from shared.clients.dms.DMSClientInterface import DMSClientInterface
from shared.clients.dms.models.DocumentUpdate import DocumentUpdateRequest
from shared.helper.HelperConfig import HelperConfig
from shared.models.config import EnvConfig
from shared.clients.dms.models.Document import DocumentsListResponse, DocumentDetails
from shared.clients.dms.models.Correspondent import CorrespondentsListResponse, CorrespondentDetails
from shared.clients.dms.models.Tag import TagsListResponse, TagDetails
from shared.clients.dms.models.Owner import OwnersListResponse, OwnerDetails
from shared.clients.dms.models.DocumentType import DocumentTypesListResponse, DocumentTypeDetails
from shared.clients.dms.models.CustomField import CustomFieldBase, CustomFieldDetails, CustomFieldsListResponse
from datetime import datetime
from urllib.parse import urlparse, parse_qs
import mimetypes

# How long to wait between task-status polls (seconds).
_TASK_POLL_INTERVAL_S: float = 2.0
# Maximum total time to wait for a task to complete before raising.
_TASK_POLL_TIMEOUT_S: float = 120.0


class DMSClientPaperless(DMSClientInterface):
    def __init__(self, helper_config: HelperConfig):
        super().__init__(helper_config=helper_config)
        self._base_url = self.get_config_val("BASE_URL", default=None, val_type="string")
        self._api_key = self.get_config_val("API_KEY", default="", val_type="string")

    ##########################################
    ################ GETTER ##################
    ##########################################

    ################ GENERAL ##################
    def _get_engine_name(self) -> str:
        return "Paperless"
    
    def get_document_view_url(self, document_id: str) -> str:
        return "%s/documents/%s/details" % (self._get_base_url().rstrip("/"), document_id)
    
    ################ CONFIG ##################
    def _get_required_config(self) -> list[EnvConfig]:
        return [
            EnvConfig(env_key="BASE_URL", val_type="string", default=None),
            EnvConfig(env_key="API_KEY", val_type="string", default="")
        ]
    
    ################ AUTH ##################
    def _get_auth_header(self) -> dict:
        if self._api_key:
            return {"Authorization": f"Token {self._api_key}"}
        else:
            return {}
    
    ################ ENDPOINTS ##################    
    def _get_base_url(self) -> str:
        return self._base_url
    
    def _get_endpoint_healthcheck(self) -> str:
        return "/api/documents/"
    
    def _get_endpoint_documents(self, page:int = 1, page_size:int=100) -> str:
        plain_url = f"/api/documents/"
        separator = "?"
        if page:
            plain_url += f"{separator}page={page}"
            separator = "&"
        if page_size:
            plain_url += f"{separator}page_size={page_size}"
        return plain_url
    
    def _get_endpoint_correspondents(self, page:int = 1, page_size:int=100) -> str:
        plain_url = f"/api/correspondents/"
        separator = "?"
        if page:
            plain_url += f"{separator}page={page}"
            separator = "&"
        if page_size:
            plain_url += f"{separator}page_size={page_size}"
        return plain_url

    def _get_endpoint_tags(self, page:int = 1, page_size:int=100) -> str:
        plain_url = f"/api/tags/"
        separator = "?"
        if page:
            plain_url += f"{separator}page={page}"
            separator = "&"
        if page_size:
            plain_url += f"{separator}page_size={page_size}"
        return plain_url
    
    def _get_endpoint_owners(self, page:int = 1, page_size:int=100) -> str:
        plain_url = f"/api/users/"
        separator = "?"
        if page:
            plain_url += f"{separator}page={page}"
            separator = "&"
        if page_size:
            plain_url += f"{separator}page_size={page_size}"
        return plain_url
    
    def _get_endpoint_document_types(self, page:int = 1, page_size:int=100) -> str:
        plain_url = f"/api/document_types/"
        separator = "?"
        if page:
            plain_url += f"{separator}page={page}"
            separator = "&"
        if page_size:
            plain_url += f"{separator}page_size={page_size}"
        return plain_url
    
    def _get_endpoint_document_details(self, document_id: str) -> str:
        return f"/api/documents/{document_id}/"
    
    def _get_endpoint_correspondent_details(self, correspondent_id: str) -> str:
        return f"/api/correspondents/{correspondent_id}/"
    
    def _get_endpoint_tag_details(self, tag_id: str) -> str:
        return f"/api/tags/{tag_id}/"
    
    def _get_endpoint_owner_details(self, owner_id: str) -> str:
        return f"/api/users/{owner_id}/"
    
    def _get_endpoint_document_type_details(self, document_type_id: str) -> str:
        return f"/api/document_types/{document_type_id}/"

    def _get_endpoint_upload(self) -> str:
        return "/api/documents/post_document/"

    def _get_endpoint_tasks(self) -> str:
        return "/api/tasks/"

    def _get_endpoint_create_correspondent(self) -> str:
        return "/api/correspondents/"

    def _get_endpoint_create_document_type(self) -> str:
        return "/api/document_types/"

    def _get_endpoint_create_tag(self) -> str:
        return "/api/tags/"

    def _get_endpoint_update_document(self, document_id: int) -> str:
        return "/api/documents/%d/" % document_id

    def _get_endpoint_delete_document(self, document_id: int) -> str:
        return "/api/documents/%d/" % document_id

    def _get_endpoint_custom_fields(self, page: int, page_size: int) -> str:
        return "/api/custom_fields/?page=%d&page_size=%d" % (page, page_size)

    def _get_endpoint_create_custom_field(self) -> str:
        return "/api/custom_fields/"

    ##########################################
    ############# PAYLOAD HOOKS ##############
    ##########################################

    def get_create_correspondent_payload(self, name: str) -> dict:
        return {"name": name}

    def get_create_document_type_payload(self, name: str) -> dict:
        return {"name": name}

    def get_create_tag_payload(self, name: str) -> dict:
        return {"name": name}

    def get_create_custom_field_payload(self, name: str, data_type: str) -> dict:
        return {"name": name, "data_type": data_type}

    def get_update_document_payload(
        self,
        document_id: int,
        update: DocumentUpdateRequest,
        custom_field_pairs: list[tuple[int, str]] | None = None,
    ) -> dict:
        payload: dict = {}
        if update.title is not None:
            payload["title"] = update.title
        if update.correspondent_id is not None:
            payload["correspondent"] = update.correspondent_id
        if update.document_type_id is not None:
            payload["document_type"] = update.document_type_id
        if update.tag_ids:
            payload["tags"] = update.tag_ids
        if update.content is not None:
            payload["content"] = update.content
        if update.created_date is not None:
            payload["created"] = update.created_date
        if update.owner_id is not None:
            payload["owner"] = update.owner_id
        if custom_field_pairs:
            # Paperless-ngx expects [{"field": <id>, "value": <str>}, ...]
            payload["custom_fields"] = [
                {"field": field_id, "value": value}
                for field_id, value in custom_field_pairs
            ]
        return payload

    ##########################################
    ############# WRITE REQUESTS #############
    ##########################################

    async def do_upload_document(
        self,
        file_bytes: bytes,
        file_name: str,
        title: str | None = None,
        correspondent_id: int | None = None,
        document_type_id: int | None = None,
        tag_ids: list[int] | None = None,
        owner_id: int | None = None,
        created_date: str | None = None,
    ) -> int:
        """Upload a document to Paperless-ngx via multipart form POST."""
        mime_type, _ = mimetypes.guess_type(file_name)
        mime_type = mime_type or "application/octet-stream"

        files = [("document", (file_name, file_bytes, mime_type))]
        data = {}
        if title:
            data["title"] = title
        if correspondent_id is not None:
            data["correspondent"] = str(correspondent_id)
        if document_type_id is not None:
            data["document_type"] = str(document_type_id)
        if tag_ids:
            data["tags"] = [str(t) for t in tag_ids]
        if owner_id is not None:
            data["owner"] = str(owner_id)
        if created_date:
            data["created_date"] = created_date

        self.logging.debug("Uploading document '%s' to Paperless-ngx...", file_name)
        response = await self.do_request(
            method="POST",
            endpoint=self._get_endpoint_upload(),
            files=files,
            data=data,
            raise_on_error=True,
        )
        result = response.json() if response.text else None

        # Paperless returns an int document ID directly (older versions) or
        # a celery task UUID string that must be polled until processing is done.
        if isinstance(result, int):
            self.logging.info(
                "Document '%s' uploaded, id=%d", file_name, result
            )
            return result
        elif isinstance(result, dict):
            doc_id = int(result.get("id", 0))
            self.logging.info("Document '%s' uploaded, id=%d", file_name, doc_id)
            return doc_id
        else:
            task_uuid = str(result)
            self.logging.debug(
                "Document '%s' accepted (task_id=%s), waiting for processing...",
                file_name, task_uuid,
            )
            return await self._poll_task_document_id(task_uuid, file_name)

    async def _poll_task_document_id(self, task_uuid: str, file_name: str) -> int:
        """Poll GET /api/tasks/?task_id=<uuid> until the document ID is available.

        Args:
            task_uuid: Celery task UUID returned by /api/documents/post_document/.
            file_name: Used for log messages only.

        Returns:
            The Paperless-ngx document ID once the task reaches SUCCESS.

        Raises:
            TimeoutError: If the task does not complete within _TASK_POLL_TIMEOUT_S.
            RuntimeError: If the task ends in a FAILURE state.
        """
        elapsed = 0.0
        while elapsed < _TASK_POLL_TIMEOUT_S:
            await asyncio.sleep(_TASK_POLL_INTERVAL_S)
            elapsed += _TASK_POLL_INTERVAL_S

            response = await self.do_request(
                method="GET",
                endpoint=self._get_endpoint_tasks(),
                params={"task_id": task_uuid},
            )
            tasks = response.json() if response.text else []
            if not tasks:
                self.logging.debug(
                    "Task %s: no result yet (%.0fs elapsed)", task_uuid, elapsed
                )
                continue

            task = tasks[0] if isinstance(tasks, list) else tasks
            status = task.get("status", "")
            related_document = task.get("related_document")

            self.logging.debug(
                "Task %s: status=%s, related_document=%s (%.0fs elapsed)",
                task_uuid, status, related_document, elapsed,
            )

            if status == "SUCCESS" and related_document:
                doc_id = int(related_document)
                self.logging.info(
                    "Document '%s' processed successfully, id=%d", file_name, doc_id
                )
                return doc_id

            if status == "FAILURE":
                result_msg = task.get("result", "unknown error")
                if "duplicate" in result_msg.lower():
                    # Try related_document field first (Paperless >= ~1.17),
                    # fall back to parsing the ID from the result string.
                    dup_id: int | None = None
                    if related_document:
                        dup_id = int(related_document)
                    else:
                        import re
                        m = re.search(r"\(#(\d+)\)", result_msg)
                        if m:
                            dup_id = int(m.group(1))
                    raise FileExistsError(dup_id)
                raise RuntimeError(
                    "Paperless-ngx task %s failed for '%s': %s"
                    % (task_uuid, file_name, result_msg)
                )

        raise TimeoutError(
            "Paperless-ngx task %s for '%s' did not complete within %.0f seconds"
            % (task_uuid, file_name, _TASK_POLL_TIMEOUT_S)
        )

    async def do_delete_document(self, document_id: int) -> bool:
        """Delete a document from Paperless-ngx."""
        await self.do_request(
            method="DELETE",
            endpoint=self._get_endpoint_delete_document(document_id),
            raise_on_error=True,
        )
        return True

    async def do_fetch_custom_fields(self) -> list[CustomFieldBase]:
        """Fetch all custom field definitions from Paperless-ngx, paginating like tags."""
        custom_fields: list[CustomFieldBase] = []
        page = 1
        page_size = 300
        while True:
            resp = await self.do_request(
                method="GET",
                endpoint=self._get_endpoint_custom_fields(page=page, page_size=page_size),
            )
            list_response = self._parse_endpoint_custom_fields(resp.json())
            custom_fields.extend(list_response.custom_fields)
            self.logging.debug(
                "Fetched custom fields page %d from %s, total so far: %d of %d",
                page, self._get_engine_name(), len(custom_fields), list_response.overallCount,
            )
            page = list_response.nextPage
            if not page:
                break
        self.logging.info(
            "Fetched all custom fields from %s, total: %d.",
            self._get_engine_name(), len(custom_fields),
        )
        return custom_fields

    async def do_create_custom_field(self, name: str, data_type: str = "string") -> int:
        """Create a new custom field definition in Paperless-ngx.

        Args:
            name (str): Display name for the new field.
            data_type (str): Paperless-ngx data type identifier (default 'string').

        Returns:
            int: The newly created field's ID.
        """
        payload = self.get_create_custom_field_payload(name, data_type)
        response = await self.do_request(
            method="POST",
            endpoint=self._get_endpoint_create_custom_field(),
            json=payload,
            raise_on_error=True,
        )
        return self._parse_endpoint_create_custom_field(response.json())

    ##########################################
    ########### RESPONSE PARSER ##############
    ##########################################

    ############# WRITE RESPONSES ############
    def _parse_endpoint_create_correspondent(self, response: dict) -> int:
        return int(response["id"])

    def _parse_endpoint_create_document_type(self, response: dict) -> int:
        return int(response["id"])

    def _parse_endpoint_create_tag(self, response: dict) -> int:
        return int(response["id"])

    def _parse_endpoint_create_custom_field(self, response: dict) -> int:
        return int(response["id"])

    def _parse_endpoint_update_document(self, response: dict) -> bool:
        return True

    ############### LIST RESPONSES ###############
    def _parse_endpoint_documents(self, response: dict, requested_page_size:int|None = None) -> DocumentsListResponse:
        meta = self._parse_listing_meta(response)
        
        # since paperless sends details in the list, we can parse them directly into DocumentDetails objects
        docs = []
        for item in response.get("results", []):
            doc = self._parse_endpoint_document(item)
            docs.append(doc)

        # return answer
        pageLen = len(docs) if not requested_page_size else requested_page_size
        return DocumentsListResponse(
            engine=self._get_engine_name(),
            documents=docs,
            currentPage=meta["current_page"],
            nextPage=meta["next_page"],
            previousPage=meta["previous_page"],
            overallCount=meta["overall_results_count"],
            pageLength=pageLen,
            lastPage= meta["overall_results_count"] // pageLen + (1 if meta["overall_results_count"] % pageLen > 0 else 0) if meta["overall_results_count"] and pageLen else None
        )
    
    def _parse_endpoint_correspondents(self, response: dict, requested_page_size:int|None = None) -> CorrespondentsListResponse:
        meta = self._parse_listing_meta(response)
        
        # since paperless sends details in the list, we can parse them directly into CorrespondentDetails objects
        correspondents = []
        for item in response.get("results", []):
            correspondent = self._parse_endpoint_correspondent(item)
            correspondents.append(correspondent)

        # return answer
        pageLen = len(correspondents) if not requested_page_size else requested_page_size
        return CorrespondentsListResponse(
            engine=self._get_engine_name(),
            correspondents=correspondents,
            currentPage=meta["current_page"],
            nextPage=meta["next_page"],
            previousPage=meta["previous_page"],
            overallCount=meta["overall_results_count"],
            pageLength=pageLen,
            lastPage= meta["overall_results_count"] // pageLen + (1 if meta["overall_results_count"] % pageLen > 0 else 0) if meta["overall_results_count"] and pageLen else None
        )
    
    def _parse_endpoint_owners(self, response: dict, requested_page_size:int|None = None) -> OwnersListResponse:
        meta = self._parse_listing_meta(response)
        
        # since paperless sends details in the list, we can parse them directly into OwnerDetails objects
        owners = []
        for item in response.get("results", []):
            owner = self._parse_endpoint_owner(item)
            owners.append(owner)

        # return answer
        pageLen = len(owners) if not requested_page_size else requested_page_size
        return OwnersListResponse(
            engine=self._get_engine_name(),
            owners=owners,
            currentPage=meta["current_page"],
            nextPage=meta["next_page"],
            previousPage=meta["previous_page"],
            overallCount=meta["overall_results_count"],
            pageLength=pageLen,
            lastPage= meta["overall_results_count"] // pageLen + (1 if meta["overall_results_count"] % pageLen > 0 else 0) if meta["overall_results_count"] and pageLen else None
        )
        
    
    def _parse_endpoint_tags(self, response: dict, requested_page_size:int|None = None) -> TagsListResponse:
        meta = self._parse_listing_meta(response)
        
        # since paperless sends details in the list, we can parse them directly into TagDetails objects
        tags = []
        for item in response.get("results", []):
            tag = self._parse_endpoint_tag(item)
            tags.append(tag)

        # return answer
        pageLen = len(tags) if not requested_page_size else requested_page_size
        return TagsListResponse(
            engine=self._get_engine_name(),
            tags=tags,
            currentPage=meta["current_page"],
            nextPage=meta["next_page"],
            previousPage=meta["previous_page"],
            overallCount=meta["overall_results_count"],
            pageLength=pageLen,
            lastPage= meta["overall_results_count"] // pageLen + (1 if meta["overall_results_count"] % pageLen > 0 else 0) if meta["overall_results_count"] and pageLen else None
        )
    
    def _parse_endpoint_document_types(self, response: dict, requested_page_size:int|None = None) -> DocumentTypesListResponse:
        meta = self._parse_listing_meta(response)
        
        # since paperless sends details in the list, we can parse them directly into DocumentTypeDetails objects
        types = []
        for item in response.get("results", []):
            doc_type = self._parse_endpoint_document_type(item)
            types.append(doc_type)

        # return answer
        pageLen = len(types) if not requested_page_size else requested_page_size
        return DocumentTypesListResponse(
            engine=self._get_engine_name(),
            types=types,
            currentPage=meta["current_page"],
            nextPage=meta["next_page"],
            previousPage=meta["previous_page"],
            overallCount=meta["overall_results_count"],
            pageLength=pageLen,
            lastPage= meta["overall_results_count"] // pageLen + (1 if meta["overall_results_count"] % pageLen > 0 else 0) if meta["overall_results_count"] and pageLen else None
        )
    
    def _parse_endpoint_custom_fields(self, response: dict) -> CustomFieldsListResponse:
        meta = self._parse_listing_meta(response)

        # each result item is parsed into a CustomFieldDetails — same pattern as tags
        custom_fields = []
        for item in response.get("results", []):
            custom_fields.append(self._parse_endpoint_custom_field(item))

        return CustomFieldsListResponse(
            engine=self._get_engine_name(),
            custom_fields=custom_fields,
            currentPage=meta["current_page"],
            nextPage=meta["next_page"],
            overallCount=meta["overall_results_count"] or 0,
        )

    def _parse_endpoint_custom_field(self, response: dict) -> CustomFieldDetails:
        return CustomFieldDetails(
            engine=self._get_engine_name(),
            id=str(response.get("id")),
            name=response.get("name"),
        )

    def _parse_listing_meta(self, listing_response:dict) -> dict:
        """
        Parse the metadata from a listing response, including pagination details.

        Args:
            listing_response (dict): The raw response from the DMS listing endpoint.

        Returns:
            dict: A dictionary containing pagination metadata such as current_page, next_page, previous_page, and overall_results_count.
        """
        next_url = listing_response.get("next")
        overall_results_count = listing_response.get("count")
        next_page: int | None = None
        if next_url:
            params = parse_qs(urlparse(next_url).query)
            page_values = params.get("page", [])
            if page_values and page_values[0].isdigit():
                next_page = int(page_values[0])
        current_page = next_page - 1 if next_page else 1
        previous_page = current_page - 1 if current_page > 1 else None

        return {
            "current_page": current_page,
            "next_page": next_page,
            "previous_page": previous_page,
            "overall_results_count": overall_results_count
        }
        
    
    ############### GET RESPONSES ###############
    def _parse_endpoint_document(self, response: dict) -> DocumentDetails:
        return DocumentDetails(
                #base
                engine=self._get_engine_name(),
                id=str(response.get("id")),

                #details
                correspondent_id=str(v) if (v := response.get("correspondent")) is not None else None,
                document_type_id=str(v) if (v := response.get("document_type")) is not None else None,
                title=response.get("title"),
                content=response.get("content"),
                tag_ids=[str(t) for t in response.get("tags", [])],
                created_date=datetime.fromisoformat(response.get("created_date")) if response.get("created_date") else None,
                owner_id=str(v) if (v := response.get("owner")) is not None else None,
                mime_type=response.get("mime_type"),
                file_name=response.get("original_file_name"),
                # capture raw [{"field": id, "value": "..."}] pairs as {str(id): value} so
                # fill_cache() can resolve field names using the custom field definition cache
                custom_field_ids={
                    str(item["field"]): item.get("value", "")
                    for item in response.get("custom_fields", [])
                    if item.get("field") is not None
                },
            )

    def _parse_endpoint_correspondent(self, response: dict) -> CorrespondentDetails:
        return CorrespondentDetails(
                #base
                engine=self._get_engine_name(),
                id=str(response.get("id")),

                #details
                name=response.get("name"),
                slug=response.get("slug"),
                owner_id=str(v) if (v := response.get("owner")) is not None else None,
                documents=response.get("documents_count")
            )

    def _parse_endpoint_owner(self, response: dict) -> OwnerDetails:
        return OwnerDetails(
                #base
                engine=self._get_engine_name(),
                id=str(response.get("id")),

                #details
                username=response.get("username"),
                email=response.get("email"),
                firstname=response.get("first_name"),
                lastname=response.get("last_name")
            )

    def _parse_endpoint_tag(self, response: dict) -> TagDetails:
        return TagDetails(
                #base
                engine=self._get_engine_name(),
                id=str(response.get("id")),

                #details
                name=response.get("name"),
                slug=response.get("slug"),
                owner_id=str(v) if (v := response.get("owner")) is not None else None,
                documents=response.get("documents_count")
            )

    def _parse_endpoint_document_type(self, response: dict) -> DocumentTypeDetails:
        return DocumentTypeDetails(
                #base
                engine=self._get_engine_name(),
                id=str(response.get("id")),

                #details
                name=response.get("name"),
                slug=response.get("slug"),
                owner_id=str(v) if (v := response.get("owner")) is not None else None,
                documents=response.get("documents_count")
            )