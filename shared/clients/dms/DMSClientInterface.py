from abc import abstractmethod
from shared.helper.HelperConfig import HelperConfig
from shared.clients.ClientInterface import ClientInterface
from shared.clients.dms.models.Document import DocumentBase, DocumentDetails, DocumentsListResponse, DocumentHighDetails
from shared.clients.dms.models.Correspondent import CorrespondentBase, CorrespondentDetails, CorrespondentsListResponse
from shared.clients.dms.models.Tag import TagBase, TagDetails, TagsListResponse
from shared.clients.dms.models.Owner import OwnerBase, OwnerDetails, OwnersListResponse
from shared.clients.dms.models.DocumentType import DocumentTypeBase, DocumentTypeDetails, DocumentTypesListResponse
from shared.clients.dms.models.DocumentUpdate import DocumentUpdateRequest
from shared.clients.dms.models.CustomField import CustomFieldBase, CustomFieldDetails, CustomFieldsListResponse
from typing import Callable, TypeVar
T = TypeVar("T")

class DMSClientInterface(ClientInterface):
    def __init__(self, helper_config: HelperConfig):
        super().__init__(helper_config=helper_config)

        # cache
        self._cache_documents: dict[str, DocumentDetails] | None = None
        self._cache_correspondents: dict[str, CorrespondentDetails] | None = None
        self._cache_tags: dict[str, TagDetails] | None = None
        self._cache_owners: dict[str, OwnerDetails] | None = None
        self._cache_document_types: dict[str, DocumentTypeDetails] | None = None
        self._cache_enriched_documents: dict[str, DocumentHighDetails] | None = None
        # key = field id (str) — populated during fill_cache() alongside other metadata caches
        self._cache_custom_fields: dict[str, CustomFieldDetails] = {}

    ##########################################
    ############### CHECKER ##################
    ##########################################

    ##########################################
    ################ GETTER ##################
    ##########################################

    ################ GENERAL ##################
    def _get_client_type(self) -> str:
        """
        Returns the type of the client. E.g. "rag"
        """
        return "dms"
    
    def get_enriched_documents(self) -> list[DocumentHighDetails]:
        """
        Returns the enriched document details cache, which contains all the information needed for the LLM prompt in one place. This is filled during the fill_cache() method.

        Returns:
            list[DocumentHighDetails]: A list of enriched document details.

        """
        if self._cache_enriched_documents is None:
            raise Exception("Enriched document cache is not filled yet. Please call fill_cache() first.")
        return list(self._cache_enriched_documents.values())
    
    @abstractmethod
    def get_document_view_url(self, document_id: str) -> str:
        """Returns the URL to view the document with the given ID in the DMS frontend."""

    ################ ENDPOINTS ##################
    @abstractmethod
    def _get_endpoint_documents(self, page:int = 1, page_size:int=100) -> str:
        """
        Returns the endpoint path for document listing requests.
        
        Args:
            page (int): The page number for paginated document listing.
            page_size (int): The number of documents per page for paginated document listing.

        Returns:
            str: The endpoint path for document listing requests (e.g. "/api/documents")

        Raises:
            NotImplementedError: If the method is not implemented in a subclass.
        """
        pass

    @abstractmethod
    def _get_endpoint_correspondents(self, page:int = 1, page_size:int=100) -> str:
        """
        Returns the endpoint path for correspondent requests.
        
        Args:
            page (int): The page number for paginated correspondent listing.
            page_size (int): The number of correspondents per page for paginated correspondent listing.

        Returns:
            str: The endpoint path for correspondent requests (e.g. "/api/correspondents")

        Raises:
            NotImplementedError: If the method is not implemented in a subclass.
        """
        pass

    @abstractmethod
    def _get_endpoint_tags(self, page:int = 1, page_size:int=100) -> str:
        """
        Returns the endpoint path for tag requests.
        
        Args:
            page (int): The page number for paginated tag listing.
            page_size (int): The number of tags per page for paginated tag listing.

        Returns:
            str: The endpoint path for tag requests (e.g. "/api/tags")

        Raises:
            NotImplementedError: If the method is not implemented in a subclass.
        """
        pass

    @abstractmethod
    def _get_endpoint_owners(self, page:int = 1, page_size:int=100) -> str:
        """
        Returns the endpoint path for owner requests.
        
        Args:
            page (int): The page number for paginated owner listing.
            page_size (int): The number of owners per page for paginated owner listing.

        Returns:
            str: The endpoint path for owner requests (e.g. "/api/owners")

        Raises:
            NotImplementedError: If the method is not implemented in a subclass.
        """
        pass

    @abstractmethod
    def _get_endpoint_document_types(self, page:int = 1, page_size:int=100) -> str:
        """
        Returns the endpoint path for document type requests.
        
        Args:
            page (int): The page number for paginated document type listing.
            page_size (int): The number of document types per page for paginated document type listing.

        Returns:
            str: The endpoint path for document type requests (e.g. "/api/document_types")

        Raises:
            NotImplementedError: If the method is not implemented in a subclass.
        """
        pass

    @abstractmethod
    def _get_endpoint_document_details(self, document_id: str) -> str:
        """
        Returns the endpoint path for document details requests.
        
        Args:
            document_id (str): The ID of the document.

        Returns:
            str: The endpoint path for document details requests (e.g. "/api/documents/{id}")

        Raises:
            NotImplementedError: If the method is not implemented in a subclass.
        """
        pass

    @abstractmethod
    def _get_endpoint_correspondent_details(self, correspondent_id: str) -> str:
        """
        Returns the endpoint path for correspondent details requests.
        
        Args:
            correspondent_id (str): The ID of the correspondent.

        Returns:
            str: The endpoint path for correspondent details requests (e.g. "/api/correspondents/{id}")

        Raises:
            NotImplementedError: If the method is not implemented in a subclass.
        """
        pass

    @abstractmethod
    def _get_endpoint_tag_details(self, tag_id: str) -> str:
        """
        Returns the endpoint path for tag details requests.
        
        Args:
            tag_id (str): The ID of the tag.

        Returns:
            str: The endpoint path for tag details requests (e.g. "/api/tags/{id}")

        Raises:
            NotImplementedError: If the method is not implemented in a subclass.
        """
        pass

    @abstractmethod
    def _get_endpoint_owner_details(self, owner_id: str) -> str:
        """
        Returns the endpoint path for owner details requests.
        
        Args:
            owner_id (str): The ID of the owner.

        Returns:
            str: The endpoint path for owner details requests (e.g. "/api/owners/{id}")

        Raises:
            NotImplementedError: If the method is not implemented in a subclass.
        """
        pass

    @abstractmethod
    def _get_endpoint_document_type_details(self, document_type_id: str) -> str:
        """
        Returns the endpoint path for document type details requests.

        Args:
            document_type_id (str): The ID of the document type.

        Returns:
            str: The endpoint path for document type details requests (e.g. "/api/document_types/{id}")

        Raises:
            NotImplementedError: If the method is not implemented in a subclass.
        """
        pass

    @abstractmethod
    def _get_endpoint_create_correspondent(self) -> str:
        """
        Returns the endpoint path for creating correspondents on the dms system

        Returns:
            str: The endpoint path for creating correspondents (e.g. "/api/correspondents")

        Raises:
            NotImplementedError: If the method is not implemented in a subclass.
        """
        pass

    @abstractmethod
    def _get_endpoint_create_document_type(self) -> str:
        """
        Returns the endpoint path for creating document types on the dms system

        Returns:
            str: The endpoint path for creating document types (e.g. "/api/document_types")

        Raises:
            NotImplementedError: If the method is not implemented in a subclass.
        """
        pass

    @abstractmethod
    def _get_endpoint_create_tag(self) -> str:
        """
        Returns the endpoint path for creating tags on the dms system

        Returns:
            str: The endpoint path for creating tags (e.g. "/api/tags")

        Raises:
            NotImplementedError: If the method is not implemented in a subclass.
        """
        pass

    @abstractmethod
    def _get_endpoint_update_document(self, document_id: int) -> str:
        """
        Returns the endpoint path for updating documents on the dms system

        Args:
            document_id (int): The ID of the document.

        Returns:
            str: The endpoint path for updating documents (e.g. "/api/documents/{id}")

        Raises:
            NotImplementedError: If the method is not implemented in a subclass.
        """
        pass

    @abstractmethod
    def _get_endpoint_custom_fields(self, page: int, page_size: int) -> str:
        """
        Returns the endpoint path for paginated custom field definition listing.

        Args:
            page (int): The page number for paginated listing.
            page_size (int): The number of items per page.

        Returns:
            str: The endpoint path (e.g. '/api/custom_fields/?page=1&page_size=300').

        Raises:
            NotImplementedError: If the method is not implemented in a subclass.
        """
        pass

    @abstractmethod
    def _get_endpoint_create_custom_field(self) -> str:
        """
        Returns the endpoint path for creating a new custom field definition.

        Returns:
            str: The endpoint path (e.g. '/api/custom_fields/').

        Raises:
            NotImplementedError: If the method is not implemented in a subclass.
        """
        pass

    ##########################################
    ############# PAYLOAD HOOKS ##############
    ##########################################

    @abstractmethod
    def get_create_correspondent_payload(self, name: str) -> dict:
        """
        Returns the payload for creating a new correspondent on the dms system. 
        This is used in the do_create_correspondent() method.

        Args:
            name (str): The name of the correspondent to create.

        Returns:
            dict: The payload for the create correspondent request.
        """
        pass

    @abstractmethod
    def get_create_document_type_payload(self, name: str) -> dict:
        """
        Returns the payload for creating a new document type on the dms system. 
        This is used in the do_create_document_type() method.

        Args:
            name (str): The name of the document type to create.

        Returns:
            dict: The payload for the create document type request.
        """
        pass

    @abstractmethod
    def get_create_tag_payload(self, name: str) -> dict:
        """
        Returns the payload for creating a new tag on the dms system. 
        This is used in the do_create_tag() method.

        Args:
            name (str): The name of the tag to create.

        Returns:
            dict: The payload for the create tag request.
        """
        pass

    @abstractmethod
    def get_create_custom_field_payload(self, name: str, data_type: str) -> dict:
        """
        Returns the payload for creating a new custom field definition on the DMS.

        Args:
            name (str): Display name for the field.
            data_type (str): Data type identifier (e.g. 'string').

        Returns:
            dict: The payload for the create custom field request.
        """
        pass

    @abstractmethod
    def get_update_document_payload(
        self,
        document_id: int,
        update: DocumentUpdateRequest,
        custom_field_pairs: list[tuple[int, str]] | None = None,
    ) -> dict:
        """
        Returns the payload for updating a document on the DMS.

        Args:
            document_id (int): The ID of the document to update.
            update (DocumentUpdateRequest): Fields to set. None values are omitted.
            custom_field_pairs (list[tuple[int, str]] | None): Pre-resolved
                (field_id, value) pairs to include in the update. When non-empty,
                the backend serialises them into its native custom-field format.

        Returns:
            dict: The payload for the update document request.
        """
        pass

    ##########################################
    ############### REQUESTS #################
    ##########################################
    
    ############# LISTING REQUESTS ##############
    async def do_fetch_documents(self) -> list[DocumentBase]:
        """
        Fetches all documents from dms backend

        Returns:
            list[DocumentBase]: A list of documents fetched from the backend.
        Raises:
            Exception: If the response format is invalid or the content cannot be extracted.
            NotImplementedError: If the method is not implemented in a subclass.
        """
        documents = []
        page = 1
        page_size = 300
        while True:
            resp = await self.do_request(method="GET", endpoint=self._get_endpoint_documents(page=page, page_size=page_size))
            documents_list_response = self._parse_endpoint_documents(resp.json(), requested_page_size=page_size)
            documents.extend(documents_list_response.documents)
            self.logging.debug("Fetched documents page %d of %d from %s, total documents so far: %d of %d", page, documents_list_response.lastPage, self._get_engine_name(), len(documents), documents_list_response.overallCount)
            page = documents_list_response.nextPage
            if not page:
                break     
        self.logging.info("Fetched all documents from %s, total documents: %d.", self._get_engine_name(), len(documents))                  
        return documents
    
    async def do_fetch_correspondents(self) -> list[CorrespondentBase]:
        """
        Fetches all correspondents from dms backend

        Returns:
            list[CorrespondentBase]: A list of correspondents fetched from the backend.
        Raises:
            Exception: If the response format is invalid or the content cannot be extracted.
            NotImplementedError: If the method is not implemented in a subclass.
        """
        correspondents = []
        page = 1
        page_size = 300
        while True:
            resp = await self.do_request(method="GET", endpoint=self._get_endpoint_correspondents(page=page, page_size=page_size))
            correspondents_list_response = self._parse_endpoint_correspondents(resp.json(), requested_page_size=page_size)
            correspondents.extend(correspondents_list_response.correspondents)
            self.logging.debug("Fetched correspondents page %d of %d from %s, total correspondents so far: %d of %d", page, correspondents_list_response.lastPage, self._get_engine_name(), len(correspondents), correspondents_list_response.overallCount)
            page = correspondents_list_response.nextPage
            if not page:
                break        
        self.logging.info("Fetched all correspondents from %s, total correspondents: %d.", self._get_engine_name(), len(correspondents))               
        return correspondents
    
    async def do_fetch_owners(self) -> list[OwnerBase]:
        """
        Fetches all owners from dms backend

        Returns:
            list[OwnerBase]: A list of owners fetched from the backend.
        Raises:
            Exception: If the response format is invalid or the content cannot be extracted.
            NotImplementedError: If the method is not implemented in a subclass.
        """
        owners = []
        page = 1
        page_size = 300
        while True:
            resp = await self.do_request(method="GET", endpoint=self._get_endpoint_owners(page=page, page_size=page_size))
            owners_list_response = self._parse_endpoint_owners(resp.json(), requested_page_size=page_size)
            owners.extend(owners_list_response.owners)
            self.logging.debug("Fetched owners page %d of %d from %s, total owners so far: %d of %d", page, owners_list_response.lastPage, self._get_engine_name(), len(owners), owners_list_response.overallCount)
            page = owners_list_response.nextPage
            if not page:
                break 
        self.logging.info("Fetched all owners from %s, total owners: %d.", self._get_engine_name(), len(owners))           
        return owners
    
    async def do_fetch_tags(self) -> list[TagBase]:
        """
        Fetches all tags from dms backend

        Returns:
            list[TagBase]: A list of tags fetched from the backend.
        Raises:
            Exception: If the response format is invalid or the content cannot be extracted.
            NotImplementedError: If the method is not implemented in a subclass.
        """
        tags = []
        page = 1
        page_size = 300
        while True:
            resp = await self.do_request(method="GET", endpoint=self._get_endpoint_tags(page=page, page_size=page_size))
            tags_list_response = self._parse_endpoint_tags(resp.json(), requested_page_size=page_size)
            tags.extend(tags_list_response.tags)
            self.logging.debug("Fetched tags page %d of %d from %s, total tags so far: %d of %d", page, tags_list_response.lastPage, self._get_engine_name(), len(tags), tags_list_response.overallCount)
            page = tags_list_response.nextPage
            if not page:
                break         
        self.logging.info("Fetched all tags from %s, total tags: %d.", self._get_engine_name(), len(tags))              
        return tags
    
    async def do_fetch_document_types(self) -> list[DocumentTypeBase]:
        """
        Fetches all document types from dms backend

        Returns:
            list[DocumentTypeBase]: A list of document types fetched from the backend.
        Raises:
            Exception: If the response format is invalid or the content cannot be extracted.
            NotImplementedError: If the method is not implemented in a subclass.
        """
        document_types = []
        page = 1
        page_size = 300
        while True:
            resp = await self.do_request(method="GET", endpoint=self._get_endpoint_document_types(page=page, page_size=page_size))
            document_types_list_response = self._parse_endpoint_document_types(resp.json(), requested_page_size=page_size)
            document_types.extend(document_types_list_response.types)
            self.logging.debug("Fetched document types page %d of %d from %s, total document types so far: %d of %d", page, document_types_list_response.lastPage, self._get_engine_name(), len(document_types), document_types_list_response.overallCount)
            page = document_types_list_response.nextPage
            if not page:
                break            
        self.logging.info("Fetched all document types from %s, total document types: %d.", self._get_engine_name(), len(document_types))              
        return document_types
    
    
    ############# GET REQUESTS ##############
    async def do_fetch_document_details(self, document_id: str) -> DocumentDetails:
        """
        Fetches a document from dms backend

        Args:
            document_id (str): The ID of the document to fetch.

        Returns:
            DocumentDetails: The details of the fetched document.
        Raises:
            Exception: If the response format is invalid or the content cannot be extracted.
            NotImplementedError: If the method is not implemented in a subclass.
        """
        resp = await self.do_request(method="GET", endpoint=self._get_endpoint_document_details(document_id))
        document_details = self._parse_endpoint_document(resp.json())
        return document_details
    
    async def do_fetch_correspondent_details(self, correspondent_id: str) -> CorrespondentDetails:
        """
        Fetches a correspondent from dms backend

        Args:
            correspondent_id (str): The ID of the correspondent to fetch.

        Returns:
            CorrespondentDetails: The details of the fetched correspondent.
        Raises:
            Exception: If the response format is invalid or the content cannot be extracted.
            NotImplementedError: If the method is not implemented in a subclass.
        """
        resp = await self.do_request(method="GET", endpoint=self._get_endpoint_correspondent_details(correspondent_id))
        correspondent_details = self._parse_endpoint_correspondent(resp.json())
        return correspondent_details
    
    async def do_fetch_owner_details(self, owner_id: str) -> OwnerDetails:
        """
        Fetches an owner from dms backend

        Args:
            owner_id (str): The ID of the owner to fetch.

        Returns:
            OwnerDetails: The details of the fetched owner.
        Raises:
            Exception: If the response format is invalid or the content cannot be extracted.
            NotImplementedError: If the method is not implemented in a subclass.
        """
        resp = await self.do_request(method="GET", endpoint=self._get_endpoint_owner_details(owner_id))
        owner_details = self._parse_endpoint_owner(resp.json())
        return owner_details
    
    async def do_fetch_tag_details(self, tag_id: str) -> TagDetails:
        """
        Fetches a tag from dms backend

        Args:
            tag_id (str): The ID of the tag to fetch.

        Returns:
            TagDetails: The details of the fetched tag.
        Raises:
            Exception: If the response format is invalid or the content cannot be extracted.
            NotImplementedError: If the method is not implemented in a subclass.
        """
        resp = await self.do_request(method="GET", endpoint=self._get_endpoint_tag_details(tag_id))
        tag_details = self._parse_endpoint_tag(resp.json())
        return tag_details
    
    async def do_fetch_document_type_details(self, document_type_id: str) -> DocumentTypeDetails:
        """
        Fetches a document type from dms backend

        Args:
            document_type_id (str): The ID of the document type to fetch.

        Returns:
            DocumentTypeDetails: The details of the fetched document type.
        Raises:
            Exception: If the response format is invalid or the content cannot be extracted.
            NotImplementedError: If the method is not implemented in a subclass.
        """
        resp = await self.do_request(method="GET", endpoint=self._get_endpoint_document_type_details(document_type_id))
        document_type_details = self._parse_endpoint_document_type(resp.json())
        return document_type_details

    ############# WRITE REQUESTS #############
    @abstractmethod
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
        """Upload a document to the DMS backend.

        Args:
            file_bytes: Raw file content.
            file_name: Original filename including extension.
            title: Document title (optional).
            correspondent_id: Existing correspondent ID (optional).
            document_type_id: Existing document type ID (optional).
            tag_ids: List of existing tag IDs (optional).
            owner_id: DMS owner ID (optional).
            created_date: Document date in ISO format YYYY-MM-DD (optional).

        Returns:
            int: The new DMS document ID.
        """
        pass

    @abstractmethod
    async def do_delete_document(self, document_id: int) -> bool:
        """Delete a document from the DMS backend.

        Args:
            document_id: The DMS document ID to delete.

        Returns:
            True on success.
        """
        pass

    async def do_create_correspondent(self, name: str) -> int:
        """Create a new correspondent in the DMS backend.

        Args:
            name: The correspondent name.

        Returns:
            int: The new correspondent ID.
        """
        payload = self.get_create_correspondent_payload(name)
        response = await self.do_request(
            method="POST", 
            endpoint=self._get_endpoint_create_correspondent(), 
            json=payload, 
            raise_on_error=True)
        return self._parse_endpoint_create_correspondent(response.json())

    async def do_create_document_type(self, name: str) -> int:
        """Create a new document type in the DMS backend.

        Args:
            name: The document type name.

        Returns:
            int: The new document type ID.
        """
        payload = self.get_create_document_type_payload(name)
        response = await self.do_request(
            method="POST", 
            endpoint=self._get_endpoint_create_document_type(), 
            json=payload, 
            raise_on_error=True)
        return self._parse_endpoint_create_document_type(response.json())

    async def do_create_tag(self, name: str) -> int:
        """Create a new tag in the DMS backend.

        Args:
            name: The tag name.

        Returns:
            int: The new tag ID.
        """
        payload = self.get_create_tag_payload(name)
        response = await self.do_request(
            method="POST", 
            endpoint=self._get_endpoint_create_tag(), 
            json=payload, 
            raise_on_error=True)
        return self._parse_endpoint_create_tag(response.json())

    async def do_update_document(
        self, document_id: int, update: DocumentUpdateRequest
    ) -> bool:
        """Update metadata fields of an existing document.

        When update.custom_fields is non-empty, each key (field name) is resolved
        to an integer field_id via do_resolve_or_create_custom_field() before the
        payload is built — so the backend always receives numeric IDs, not names.

        Args:
            document_id (int): The DMS document ID to update.
            update (DocumentUpdateRequest): Fields to set. None values are omitted.

        Returns:
            bool: True on success.
        """
        # resolve field names → IDs before handing off to the payload builder so
        # every backend receives the canonical (int id, str value) representation
        custom_field_pairs: list[tuple[int, str]] = []
        if update.custom_fields:
            for field_name, value in update.custom_fields.items():
                field_id = await self.do_resolve_or_create_custom_field(field_name)
                custom_field_pairs.append((field_id, value))

        payload = self.get_update_document_payload(document_id, update, custom_field_pairs)
        if not payload:
            return True
        response = await self.do_request(
            method="PATCH",
            endpoint=self._get_endpoint_update_document(document_id),
            json=payload,
            raise_on_error=True
        )
        return self._parse_endpoint_update_document(response.json())

    async def do_resolve_or_create_correspondent(self, name: str) -> int:
        """Find existing correspondent by name in cache, or create a new one.

        If the cache is empty it is loaded first. If creation fails (e.g. a unique
        constraint because the item exists but was absent from the cache), the cache
        is refreshed and the lookup retried before propagating the error.

        Args:
            name: The correspondent name to resolve or create.

        Returns:
            int: The correspondent ID (existing or newly created).
        """
        if not self._cache_correspondents:
            await self.get_correspondents()
        for corr_id, corr in (self._cache_correspondents or {}).items():
            if corr.name and corr.name.lower() == name.lower():
                self.logging.debug("Resolved correspondent '%s' → id=%s", name, corr_id)
                return int(corr_id)
        try:
            new_id = await self.do_create_correspondent(name)
            self.logging.info("Created new correspondent '%s' → id=%d", name, new_id)
            if self._cache_correspondents is not None:
                self._cache_correspondents[str(new_id)] = CorrespondentDetails(
                    engine=self._get_engine_name(), id=str(new_id), name=name
                )
            return new_id
        except Exception:
            await self.get_correspondents(force=True)
            for corr_id, corr in (self._cache_correspondents or {}).items():
                if corr.name and corr.name.lower() == name.lower():
                    self.logging.debug(
                        "Resolved correspondent '%s' after cache refresh → id=%s", name, corr_id
                    )
                    return int(corr_id)
            raise

    async def do_resolve_or_create_document_type(self, name: str) -> int:
        """Find existing document type by name in cache, or create a new one.

        If the cache is empty it is loaded first. If creation fails (e.g. a unique
        constraint because the item exists but was absent from the cache), the cache
        is refreshed and the lookup retried before propagating the error.
        """
        if not self._cache_document_types:
            await self.get_document_types()
        for type_id, doc_type in (self._cache_document_types or {}).items():
            if doc_type.name and doc_type.name.lower() == name.lower():
                self.logging.debug("Resolved document_type '%s' → id=%s", name, type_id)
                return int(type_id)
        try:
            new_id = await self.do_create_document_type(name)
            self.logging.info("Created new document_type '%s' → id=%d", name, new_id)
            if self._cache_document_types is not None:
                self._cache_document_types[str(new_id)] = DocumentTypeDetails(
                    engine=self._get_engine_name(), id=str(new_id), name=name
                )
            return new_id
        except Exception:
            await self.get_document_types(force=True)
            for type_id, doc_type in (self._cache_document_types or {}).items():
                if doc_type.name and doc_type.name.lower() == name.lower():
                    self.logging.debug(
                        "Resolved document_type '%s' after cache refresh → id=%s", name, type_id
                    )
                    return int(type_id)
            raise

    async def do_resolve_or_create_tag(self, name: str) -> int:
        """Find existing tag by name in cache, or create a new one.

        If the cache is empty it is loaded first. If creation fails (e.g. a unique
        constraint because the item exists but was absent from the cache), the cache
        is refreshed and the lookup retried before propagating the error.
        """
        if not self._cache_tags:
            await self.get_tags()
        for tag_id, tag in (self._cache_tags or {}).items():
            if tag.name and tag.name.lower() == name.lower():
                self.logging.debug("Resolved tag '%s' → id=%s", name, tag_id)
                return int(tag_id)
        try:
            new_id = await self.do_create_tag(name)
            self.logging.info("Created new tag '%s' → id=%d", name, new_id)
            if self._cache_tags is not None:
                self._cache_tags[str(new_id)] = TagDetails(
                    engine=self._get_engine_name(), id=str(new_id), name=name
                )
            return new_id
        except Exception:
            await self.get_tags(force=True)
            for tag_id, tag in (self._cache_tags or {}).items():
                if tag.name and tag.name.lower() == name.lower():
                    self.logging.debug(
                        "Resolved tag '%s' after cache refresh → id=%s", name, tag_id
                    )
                    return int(tag_id)
            raise

    @abstractmethod
    async def do_fetch_custom_fields(self) -> list[CustomFieldBase]:
        """
        Fetches all custom field definitions from the DMS.

        Returns:
            list[CustomFieldBase]: All custom field definitions available in the DMS.
        """
        pass

    @abstractmethod
    async def do_create_custom_field(self, name: str, data_type: str = "string") -> int:
        """
        Creates a new custom field definition in the DMS.

        Args:
            name (str): Display name for the field.
            data_type (str): Data type identifier (default 'string').

        Returns:
            int: The new field's ID.
        """
        pass

    def get_custom_fields(self) -> dict[str, CustomFieldDetails]:
        """
        Returns the cached custom field definitions keyed by field id.

        The cache is populated during fill_cache() — this getter is synchronous
        because the cache is already warm by the time callers need it.

        Returns:
            dict[str, CustomFieldDetails]: Custom field definitions indexed by str(id).
        """
        return self._cache_custom_fields

    async def do_resolve_or_create_custom_field(
        self, name: str, data_type: str = "string"
    ) -> int:
        """
        Resolves an existing custom field by name or creates it if absent.

        Looks up the current _cache_custom_fields by name. If not found, creates
        the field and injects the new entry into the cache so subsequent calls in
        the same session don't trigger another round-trip.

        Args:
            name (str): Custom field name to look up or create.
            data_type (str): Data type if creation is needed (default 'string').

        Returns:
            int: The field's ID (existing or newly created).
        """
        custom_fields = self.get_custom_fields()
        for cf in custom_fields.values():
            if cf.name == name:
                self.logging.debug("Resolved custom_field '%s' → id=%s", name, cf.id)
                return int(cf.id)
        # field not found in cache — create it and cache the new definition immediately
        new_id = await self.do_create_custom_field(name, data_type)
        self.logging.info("Created new custom_field '%s' → id=%d", name, new_id)
        self._cache_custom_fields[str(new_id)] = CustomFieldDetails(
            engine=self._get_engine_name(), id=str(new_id), name=name
        )
        return new_id

    ##########################################
    ########### RESPONSE PARSER ##############
    ##########################################

    ############# WRITE RESPONSES ############
    @abstractmethod
    def _parse_endpoint_create_correspondent(self, response: dict) -> int:
        """
        Parses the response from the create correspondent endpoint and returns the ID of the created correspondent.

        Args:
            response (dict): The raw response from the create correspondent endpoint.

        Returns:
            int: The ID of the created correspondent.        
        """
        pass

    @abstractmethod
    def _parse_endpoint_create_document_type(self, response: dict) -> int:
        """
        Parses the response from the create document type endpoint and returns the ID of the created document type.

        Args:
            response (dict): The raw response from the create document type endpoint.
        Returns:
            int: The ID of the created document type.        
        """
        pass

    @abstractmethod
    def _parse_endpoint_create_tag(self, response: dict) -> int:
        """
        Parses the response from the create tag endpoint and returns the ID of the created tag.

        Args:
            response (dict): The raw response from the create tag endpoint.
        Returns:
            int: The ID of the created tag.        
        """
        pass

    @abstractmethod
    def _parse_endpoint_update_document(self, response: dict) -> bool:
        """
        Parses the response from the update document endpoint and returns whether the update was successful.

        Args:
            response (dict): The raw response from the update document endpoint.
        Returns:
            bool: True if the update was successful, False otherwise.        
        """
        pass

    ############### LIST RESPONSES ###############
    @abstractmethod
    def _parse_endpoint_documents(self, response: dict, requested_page_size:int|None = None) -> DocumentsListResponse:
        """
        Parses the response from the document listing endpoint and returns a list of documents with their metadata.

        Args:
            response (dict): The raw response from the document listing endpoint.
            requested_page_size (int | None): The page size that was requested for this document listing. This is used to calculate the last page number in the pagination info. If None, the last page number will be calculated by amount of returned results.
        Returns:
            DocumentsListResponse: The parsed response containing a list of documents and pagination information.
        """
        pass

    @abstractmethod
    def _parse_endpoint_correspondents(self, response: dict, requested_page_size:int|None = None) -> CorrespondentsListResponse:
        """
        Parses the response from the correspondent listing endpoint and returns a list of correspondents with their metadata.

        Args:
            response (dict): The raw response from the correspondent listing endpoint.
            requested_page_size (int | None): The page size that was requested for this correspondent listing. This is used to calculate the last page number in the pagination info. If None, the last page number will be calculated by amount of returned results.
        Returns:
            CorrespondentsListResponse: The parsed response containing a list of correspondents and pagination information.
        """
        pass

    @abstractmethod
    def _parse_endpoint_owners(self, response: dict, requested_page_size:int|None = None) -> OwnersListResponse:
        """
        Parses the response from the owner listing endpoint and returns a list of owners with their metadata.

        Args:
            response (dict): The raw response from the owner listing endpoint.
            requested_page_size (int | None): The page size that was requested for this owner listing. This is used to calculate the last page number in the pagination info. If None, the last page number will be calculated by amount of returned results.
        Returns:
            OwnersListResponse: The parsed response containing a list of owners and pagination information.
        """
        pass

    @abstractmethod
    def _parse_endpoint_tags(self, response: dict, requested_page_size:int|None = None) -> TagsListResponse:
        """
        Parses the response from the tag listing endpoint and returns a list of tags with their metadata.

        Args:
            response (dict): The raw response from the tag listing endpoint.
            requested_page_size (int | None): The page size that was requested for this tag listing. This is used to calculate the last page number in the pagination info. If None, the last page number will be calculated by amount of returned results.
        Returns:
            TagsListResponse: The parsed response containing a list of tags and pagination information.
        """
        pass

    @abstractmethod
    def _parse_endpoint_document_types(self, response: dict, requested_page_size:int|None = None) -> DocumentTypesListResponse:
        """
        Parses the response from the document type listing endpoint and returns a list of document types with their metadata.

        Args:
            response (dict): The raw response from the document type listing endpoint.
            requested_page_size (int | None): The page size that was requested for this document type listing. This is used to calculate the last page number in the pagination info. If None, the last page number will be calculated by amount of returned results.
        Returns:
            DocumentTypesListResponse: The parsed response containing a list of document types and pagination information.
        """
        pass

    ############ GET RESPONSES ##############
    @abstractmethod
    def _parse_endpoint_document(self, response: dict) -> DocumentDetails:
        """
        Parses a raw document dict from the backend API into a DocumentDetails object.

        Args:
            response (dict): The raw document data as returned by the backend API.
        Returns:
            DocumentDetails: The parsed document object.
        Raises:
            Exception: If required fields are missing or the data format is invalid.
        """
        pass

    @abstractmethod
    def _parse_endpoint_correspondent(self, response: dict) -> CorrespondentDetails:
        """
        Parses a raw correspondent dict from the backend API into a CorrespondentDetails object.

        Args:
            response (dict): The raw correspondent data as returned by the backend API.
        Returns:
            CorrespondentDetails: The parsed correspondent object.
        Raises:
            Exception: If required fields are missing or the data format is invalid.
        """
        pass

    @abstractmethod
    def _parse_endpoint_owner(self, response: dict) -> OwnerDetails:
        """
        Parses a raw owner dict from the backend API into a OwnerDetails object.

        Args:
            response (dict): The raw owner data as returned by the backend API.
        Returns:
            OwnerDetails: The parsed owner object.
        Raises:
            Exception: If required fields are missing or the data format is invalid.
        """
        pass

    @abstractmethod
    def _parse_endpoint_tag(self, response: dict) -> TagDetails:
        """
        Parses a raw tag dict from the backend API into a TagDetails object.

        Args:
            response (dict): The raw tag data as returned by the backend API.
        Returns:
            TagDetails: The parsed tag object.
        Raises:
            Exception: If required fields are missing or the data format is invalid.
        """
        pass

    @abstractmethod
    def _parse_endpoint_document_type(self, response: dict) -> DocumentTypeDetails:
        """
        Parses a raw document type dict from the backend API into a DocumentTypeDetails object.

        Args:
            response (dict): The raw document type data as returned by the backend API.
        Returns:
            DocumentTypeDetails: The parsed document type object.
        Raises:
            Exception: If required fields are missing or the data format is invalid.
        """
        pass

    ##########################################
    ################# CACHE ##################
    ##########################################

    async def fill_cache(self, force_refresh: bool = False) -> None:
        """
        Fill the internal cache with any reference data needed for document resolution (e.g. correspondent and tag names).
        This is called once at startup before the sync to avoid redundant requests during document processing.

        Args:
            force_refresh (bool): If True, forces a cache refresh even if data is already present. This can be used to ensure the cache is up to date if there are changes in the backend after the initial fill.
        """
        await self.get_document_types(force=force_refresh)
        await self.get_owners(force=force_refresh)
        await self.get_tags(force=force_refresh)
        await self.get_correspondents(force=force_refresh)
        await self.get_documents(force=force_refresh)

        # fetch custom field definitions so they can be resolved by name during enrichment
        try:
            custom_field_list = await self.do_fetch_custom_fields()
            self._cache_custom_fields = {cf.id: cf for cf in custom_field_list if isinstance(cf, CustomFieldDetails)}
            # do_fetch_custom_fields() may return CustomFieldBase objects for backends that
            # only return id; ensure every cached entry is at least a CustomFieldDetails shell
            for cf in custom_field_list:
                if not isinstance(cf, CustomFieldDetails):
                    self._cache_custom_fields[cf.id] = CustomFieldDetails(
                        engine=cf.engine, id=cf.id
                    )
            self.logging.debug(
                "fill_cache: loaded %d custom field definition(s) from %s",
                len(self._cache_custom_fields), self._get_engine_name(),
            )
        except Exception as exc:
            # a missing custom fields endpoint is not fatal — log and continue with an
            # empty cache so that documents without custom fields still enrich correctly
            self.logging.warning(
                "fill_cache: could not load custom fields from %s — %s",
                self._get_engine_name(), exc,
            )
            self._cache_custom_fields = {}

        # check if enrichment is needed
        if self._cache_enriched_documents is not None and not force_refresh:
            return

        # now build the enriched document cache with all the details needed for the LLM prompt in one place
        enriched_documents: dict[str, DocumentHighDetails] = {}
        for document_id, document_details in self._cache_documents.items():
            correspondent = None
            owner = None
            tags = []
            document_type = None

            if document_details.correspondent_id and self._cache_correspondents and document_details.correspondent_id in self._cache_correspondents:
                correspondent = self._cache_correspondents[document_details.correspondent_id]
            if document_details.owner_id and self._cache_owners and document_details.owner_id in self._cache_owners:
                owner = self._cache_owners[document_details.owner_id]
            if document_details.tag_ids and self._cache_tags:
                for tag_id in document_details.tag_ids:
                    if tag_id in self._cache_tags:
                        tags.append(self._cache_tags[tag_id])
            if document_details.document_type_id and self._cache_document_types and document_details.document_type_id in self._cache_document_types:
                document_type = self._cache_document_types[document_details.document_type_id]

            # resolve raw (field_id, value) pairs stored on the DocumentDetails into a
            # name → value dict using the custom field definition cache built above
            resolved_custom_fields: dict[str, str] = {}
            # custom_field_ids holds raw {field_id: value} pairs set by the parser;
            # fill_cache() resolves them to {field_name: value} using the definition cache
            raw_custom_fields: dict[str, str] = getattr(document_details, "custom_field_ids", {})
            for field_id, value in raw_custom_fields.items():
                cf_def = self._cache_custom_fields.get(field_id)
                if cf_def and cf_def.name:
                    # replace the numeric id key with the human-readable field name
                    resolved_custom_fields[cf_def.name] = value
                else:
                    # definition missing from cache — keep the id as fallback key so data is not lost
                    self.logging.warning(
                        "fill_cache: custom field id=%s not found in cache for document id=%s — using id as key",
                        field_id, document_id,
                    )
                    resolved_custom_fields[field_id] = value

            enriched_document = DocumentHighDetails(
                # exclude custom_field_ids — it is an internal raw-ID field on DocumentDetails
                # that has no counterpart on DocumentHighDetails; custom_fields carries the
                # resolved name→value dict instead
                **document_details.model_dump(exclude={"custom_field_ids"}),
                correspondent=correspondent,
                owner=owner,
                tags=tags,
                document_type=document_type,
                custom_fields=resolved_custom_fields,
            )
            enriched_documents[document_id] = enriched_document

        self._cache_enriched_documents = enriched_documents

    async def get_documents(self, force: bool = False) -> dict[str, DocumentDetails]:
        """
        Fill the cache with document-related reference data if needed.
        
        Args:
            force (bool): If True, forces a cache refresh even if data is already present.

        Returns:
            dict[str, DocumentDetails]: A dictionary of documents indexed by their ID.

        Raises:
            Exception: If fetching data from the backend fails.
        """
        # if documents exists in cache, skip
        if self._cache_documents is not None and not force:
            return self._cache_documents
        
        #fetch documents via api
        documents = await self.do_fetch_documents()

        # iterate documents and fetch details for each document which is only a DocumentBase object
        detailed_documents: list[DocumentDetails] = []
        for document in documents:
            if not isinstance(document, DocumentDetails):
                detailed_document = await self.do_fetch_document_details(str(document.id))
                detailed_documents.append(detailed_document)
            else:
                detailed_documents.append(document)

        self._cache_documents = {document.id: document for document in detailed_documents}
        return self._cache_documents


    async def get_correspondents(self, force: bool = False) -> dict[str, CorrespondentDetails]:
        """
        Fill the cache with correspondent-related reference data if needed.
        
        Args:
            force (bool): If True, forces a cache refresh even if data is already present.

        Returns:
            dict[str, CorrespondentDetails]: A dictionary of correspondents indexed by their ID.

        Raises:
            Exception: If fetching data from the backend fails.
        """
        # if correspondents exists in cache, skip
        if self._cache_correspondents is not None and not force:
            return self._cache_correspondents
        
        #fetch correspondents via api
        correspondents = await self.do_fetch_correspondents()

        # iterate correspondents and fetch details for each correspondent which is only a CorrespondentBase object
        detailed_correspondents: list[CorrespondentDetails] = []
        for correspondent in correspondents:
            if not isinstance(correspondent, CorrespondentDetails):
                detailed_correspondent = await self.do_fetch_correspondent_details(str(correspondent.id))
                detailed_correspondents.append(detailed_correspondent)
            else:
                detailed_correspondents.append(correspondent)

        self._cache_correspondents = {correspondent.id: correspondent for correspondent in detailed_correspondents}
        return self._cache_correspondents
    
    async def get_owners(self, force: bool = False) -> dict[str, OwnerDetails]:
        """
        Fill the cache with owner-related reference data if needed.
        
        Args:
            force (bool): If True, forces a cache refresh even if data is already present.

        Returns:
            dict[str, OwnerDetails]: A dictionary of owners indexed by their ID.

        Raises:
            Exception: If fetching data from the backend fails.
        """
        # if owners exists in cache, skip
        if self._cache_owners is not None and not force:
            return self._cache_owners
        
        #fetch owners via api
        owners = await self.do_fetch_owners()

        # iterate owners and fetch details for each owner which is only a OwnerBase object
        detailed_owners: list[OwnerDetails] = []
        for owner in owners:
            if not isinstance(owner, OwnerDetails):
                detailed_owner = await self.do_fetch_owner_details(str(owner.id))
                detailed_owners.append(detailed_owner)
            else:
                detailed_owners.append(owner)
        
        self._cache_owners = {owner.id: owner for owner in detailed_owners}
        return self._cache_owners
    
    async def get_tags(self, force: bool = False) -> dict[str, TagDetails]:
        """
        Fill the cache with tag-related reference data if needed.
        
        Args:
            force (bool): If True, forces a cache refresh even if data is already present.

        Returns:
            dict[str, TagDetails]: A dictionary of tags indexed by their ID.

        Raises:
            Exception: If fetching data from the backend fails.
        """
        # if tags exists in cache, skip
        if self._cache_tags is not None and not force:
            return self._cache_tags
        
        #fetch tags via api
        tags = await self.do_fetch_tags()

        # iterate tags and fetch details for each tag which is only a TagBase object
        detailed_tags: list[TagDetails] = []
        for tag in tags:
            if not isinstance(tag, TagDetails):
                detailed_tag = await self.do_fetch_tag_details(str(tag.id))
                detailed_tags.append(detailed_tag)
            else:
                detailed_tags.append(tag)

        self._cache_tags = {tag.id: tag for tag in detailed_tags}
        return self._cache_tags
    
    async def get_document_types(self, force: bool = False) -> dict[str, DocumentTypeDetails]:
        """
        Fill the cache with document type-related reference data if needed.
        
        Args:
            force (bool): If True, forces a cache refresh even if data is already present.

        Returns:
            dict[str, DocumentTypeDetails]: A dictionary of document types indexed by their ID.

        Raises:
            Exception: If fetching data from the backend fails.
        """
        # if document types exists in cache, skip
        if self._cache_document_types is not None and not force:
            return self._cache_document_types
        
        #fetch document types via api
        document_types = await self.do_fetch_document_types()

        # iterate document types and fetch details for each document type which is only a DocumentTypeBase object
        detailed_document_types: list[DocumentTypeDetails] = []
        for doc_type in document_types:
            if not isinstance(doc_type, DocumentTypeDetails):
                detailed_doc_type = await self.do_fetch_document_type_details(str(doc_type.id))
                detailed_document_types.append(detailed_doc_type)
            else:
                detailed_document_types.append(doc_type)

        self._cache_document_types = {doc_type.id: doc_type for doc_type in detailed_document_types}
        return self._cache_document_types