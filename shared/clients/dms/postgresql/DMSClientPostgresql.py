import asyncpg
import hashlib
import httpx
from datetime import datetime

from shared.clients.dms.DMSClientInterface import DMSClientInterface
from shared.clients.dms.models.Document import DocumentBase, DocumentDetails, DocumentsListResponse
from shared.clients.dms.models.Correspondent import CorrespondentBase, CorrespondentDetails, CorrespondentsListResponse
from shared.clients.dms.models.Tag import TagBase, TagDetails, TagsListResponse
from shared.clients.dms.models.Owner import OwnerBase, OwnerDetails, OwnersListResponse
from shared.clients.dms.models.DocumentType import DocumentTypeBase, DocumentTypeDetails, DocumentTypesListResponse
from shared.clients.dms.models.DocumentUpdate import DocumentUpdateRequest
from shared.clients.dms.models.CustomField import CustomFieldBase, CustomFieldDetails, CustomFieldsListResponse
from shared.clients.storage.StorageClientInterface import StorageClientInterface
from shared.clients.storage.StorageClientManager import StorageClientManager
from shared.helper.HelperConfig import HelperConfig
from shared.models.config import EnvConfig


class DMSClientPostgresql(DMSClientInterface):
    """PostgreSQL implementation of DMSClientInterface.

    Uses asyncpg instead of httpx for database access — boot() and close()
    manage the connection pool rather than an HTTP session.
    Owns an internal StorageClient for file persistence (store/retrieve/delete).
    """

    def __init__(self, helper_config: HelperConfig) -> None:
        super().__init__(helper_config=helper_config)
        self._host: str = self.get_config_val("HOST", default=None, val_type="string")
        self._port: int = int(self.get_config_val("PORT", default="5432", val_type="string"))
        self._database: str = self.get_config_val("DATABASE", default=None, val_type="string")
        self._user: str = self.get_config_val("USER", default=None, val_type="string")
        self._password: str = self.get_config_val("PASSWORD", default="", val_type="string")
        self._pool_min: int = int(self.get_config_val("POOL_MIN", default="2", val_type="string"))
        self._pool_max: int = int(self.get_config_val("POOL_MAX", default="10", val_type="string"))
        self._view_url_prefix: str = self.get_config_val("VIEW_URL_PREFIX", default="", val_type="string")
        self._pool: asyncpg.Pool | None = None
        # instantiate StorageClient here so config validation runs at construction
        # time rather than deferred to boot() — misconfigured storage fails fast
        self._storage: StorageClientInterface = StorageClientManager(helper_config).get_client()

    ##########################################
    ############# LIFECYCLE ##################
    ##########################################

    async def boot(self) -> None:
        """Create the asyncpg connection pool and ensure the schema exists.

        Deliberately does NOT call super().boot() — that would create an
        httpx.AsyncClient which is never needed for a native DB backend.
        """
        self._pool = await asyncpg.create_pool(
            host=self._host,
            port=self._port,
            database=self._database,
            user=self._user,
            password=self._password,
            min_size=self._pool_min,
            max_size=self._pool_max,
        )
        await self._ensure_tables()
        await self._storage.boot()
        self.logging.info(
            "DMSClientPostgresql booted (host=%s:%d db=%s, pool %d-%d)",
            self._host, self._port, self._database, self._pool_min, self._pool_max,
        )

    async def close(self) -> None:
        """Drain the connection pool and close the storage client."""
        await self._storage.close()
        if self._pool:
            await self._pool.close()
            self._pool = None

    ##########################################
    ############# CHECKER ####################
    ##########################################

    async def do_healthcheck(self) -> httpx.Response:
        """Verify DB connectivity with a trivial query and storage health.

        Overrides the HTTP-based do_healthcheck() from ClientInterface because
        PostgreSQL uses a native connection rather than an HTTP request. Both
        DB and storage must be healthy for a 200 — a storage failure returns 500.
        """
        self._assert_connected()
        async with self._pool.acquire() as conn:  # type: ignore[union-attr]
            await conn.fetchval("SELECT 1")
        storage_resp = await self._storage.do_healthcheck()
        # propagate storage failure upward so callers can detect partial outages
        return httpx.Response(status_code=200 if storage_resp.status_code < 300 else 500)

    ##########################################
    ################ GETTER ##################
    ##########################################

    ################ GENERAL ##################

    def _get_engine_name(self) -> str:
        return "Postgresql"

    def get_document_view_url(self, document_id: str) -> str:
        """Build a browser-accessible URL for the given document.

        Uses the configured prefix when one is set; falls back to a relative
        path so callers always get a usable string even without configuration.
        """
        if self._view_url_prefix:
            return "%s/%s" % (self._view_url_prefix.rstrip("/"), document_id)
        return "/documents/%s" % document_id

    ################ CONFIG ##################

    def _get_required_config(self) -> list[EnvConfig]:
        return [
            EnvConfig(env_key="HOST", val_type="string", default=None),
            EnvConfig(env_key="PORT", val_type="string", default="5432"),
            EnvConfig(env_key="DATABASE", val_type="string", default=None),
            EnvConfig(env_key="USER", val_type="string", default=None),
            EnvConfig(env_key="PASSWORD", val_type="string", default=""),
            EnvConfig(env_key="POOL_MIN", val_type="string", default="2"),
            EnvConfig(env_key="POOL_MAX", val_type="string", default="10"),
            EnvConfig(env_key="VIEW_URL_PREFIX", val_type="string", default=""),
        ]

    ################ AUTH / HTTP STUBS ##################

    def _get_auth_header(self) -> dict:
        # PostgreSQL uses asyncpg native auth — no HTTP auth header is needed
        return {}

    def _get_base_url(self) -> str:
        # asyncpg bypasses HTTP entirely — base URL is not meaningful here
        return ""

    def _get_endpoint_healthcheck(self) -> str:
        # overridden in do_healthcheck() — this stub satisfies the abstract contract
        return ""

    ##########################################
    ############# REQUESTS ###################
    ##########################################

    ############# FETCH REQUESTS ##############

    async def do_fetch_documents(self) -> list[DocumentBase]:
        """Fetch all documents with tag and custom field associations in three bulk queries.

        Three queries are used deliberately to avoid an N+1 pattern: one for the
        documents table, one for all document_tags rows, and one for all
        document_custom_fields rows. Maps are built in Python keyed by document_id.
        """
        self._assert_connected()
        async with self._pool.acquire() as conn:  # type: ignore[union-attr]
            doc_rows = await conn.fetch(
                "SELECT id, title, content, correspondent_id, document_type_id, "
                "owner_id, created_date, mime_type, file_name FROM documents"
            )
            tag_rows = await conn.fetch(
                "SELECT document_id, tag_id FROM document_tags"
            )
            cf_rows = await conn.fetch(
                "SELECT document_id, custom_field_id, value FROM document_custom_fields"
            )

        # build a tag lookup so each document can be enriched without extra queries
        tag_map: dict[int, list[str]] = {}
        for row in tag_rows:
            tag_map.setdefault(row["document_id"], []).append(str(row["tag_id"]))

        # build a custom field lookup {document_id: {str(field_id): value}}
        cf_map: dict[int, dict[str, str]] = {}
        for row in cf_rows:
            cf_map.setdefault(row["document_id"], {})[str(row["custom_field_id"])] = row["value"]

        documents: list[DocumentBase] = []
        for row in doc_rows:
            tag_ids = tag_map.get(row["id"], [])
            custom_field_ids = cf_map.get(row["id"], {})
            documents.append(self._row_to_document_details(row, tag_ids, custom_field_ids))

        self.logging.info(
            "DMSClientPostgresql: fetched %d document(s) from database", len(documents)
        )
        return documents

    async def do_fetch_correspondents(self) -> list[CorrespondentBase]:
        """Fetch all correspondents from the database."""
        self._assert_connected()
        async with self._pool.acquire() as conn:  # type: ignore[union-attr]
            rows = await conn.fetch(
                "SELECT id, name, slug, owner_id FROM correspondents"
            )
        correspondents: list[CorrespondentBase] = [
            self._row_to_correspondent_details(row) for row in rows
        ]
        self.logging.info(
            "DMSClientPostgresql: fetched %d correspondent(s) from database",
            len(correspondents),
        )
        return correspondents

    async def do_fetch_tags(self) -> list[TagBase]:
        """Fetch all tags from the database."""
        self._assert_connected()
        async with self._pool.acquire() as conn:  # type: ignore[union-attr]
            rows = await conn.fetch("SELECT id, name, slug, owner_id FROM tags")
        tags: list[TagBase] = [self._row_to_tag_details(row) for row in rows]
        self.logging.info(
            "DMSClientPostgresql: fetched %d tag(s) from database", len(tags)
        )
        return tags

    async def do_fetch_owners(self) -> list[OwnerBase]:
        """Fetch all owners from the database."""
        self._assert_connected()
        async with self._pool.acquire() as conn:  # type: ignore[union-attr]
            rows = await conn.fetch(
                "SELECT id, username, email, firstname, lastname FROM owners"
            )
        owners: list[OwnerBase] = [self._row_to_owner_details(row) for row in rows]
        self.logging.info(
            "DMSClientPostgresql: fetched %d owner(s) from database", len(owners)
        )
        return owners

    async def do_fetch_document_types(self) -> list[DocumentTypeBase]:
        """Fetch all document types from the database."""
        self._assert_connected()
        async with self._pool.acquire() as conn:  # type: ignore[union-attr]
            rows = await conn.fetch(
                "SELECT id, name, slug, owner_id FROM document_types"
            )
        doc_types: list[DocumentTypeBase] = [
            self._row_to_document_type_details(row) for row in rows
        ]
        self.logging.info(
            "DMSClientPostgresql: fetched %d document type(s) from database",
            len(doc_types),
        )
        return doc_types

    async def do_fetch_document_details(self, document_id: str) -> DocumentDetails:
        """Fetch a single document with its tag IDs and custom field IDs by primary key."""
        self._assert_connected()
        async with self._pool.acquire() as conn:  # type: ignore[union-attr]
            doc_row = await conn.fetchrow(
                "SELECT id, title, content, correspondent_id, document_type_id, "
                "owner_id, created_date, mime_type, file_name "
                "FROM documents WHERE id=$1",
                int(document_id),
            )
            tag_rows = await conn.fetch(
                "SELECT tag_id FROM document_tags WHERE document_id=$1",
                int(document_id),
            )
            cf_rows = await conn.fetch(
                "SELECT custom_field_id, value FROM document_custom_fields WHERE document_id=$1",
                int(document_id),
            )
        if doc_row is None:
            raise ValueError(
                "DMSClientPostgresql: document id=%s not found" % document_id
            )
        tag_ids = [str(row["tag_id"]) for row in tag_rows]
        custom_field_ids = {str(row["custom_field_id"]): row["value"] for row in cf_rows}
        return self._row_to_document_details(doc_row, tag_ids, custom_field_ids)

    ############# WRITE REQUESTS #############

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
        mime_type: str | None = None,
    ) -> int:
        """Persist file bytes to storage then insert a document record in a transaction.

        Storage is written first: if the DB insert fails afterwards we can still
        clean up the orphaned file, but if DB succeeds and storage fails we would
        have a record pointing at missing bytes. Failing fast at the storage step
        keeps the system consistent.
        """
        self._assert_connected()

        # hash the raw bytes before storage so the content_hash reflects the actual
        # file content — independent of filename, metadata, or storage location
        content_hash = hashlib.sha256(file_bytes).hexdigest()

        # store file bytes first so DB record is only created once we have a valid ref
        storage_ref = await self._storage.do_store(file_bytes, file_name)
        self.logging.debug(
            "DMSClientPostgresql: stored file '%s' → storage_ref='%s'",
            file_name, storage_ref,
        )

        # parse created_date string to a datetime if provided — asyncpg expects a datetime
        parsed_date: datetime | None = None
        if created_date:
            try:
                parsed_date = datetime.fromisoformat(created_date)
            except ValueError:
                self.logging.warning(
                    "DMSClientPostgresql: could not parse created_date '%s', storing NULL",
                    created_date,
                )

        async with self._pool.acquire() as conn:  # type: ignore[union-attr]
            async with conn.transaction():
                if owner_id is not None:
                    # auto-create the owner row if it does not yet exist — the PostgreSQL
                    # DB is self-managed (no external user directory like Paperless), so the
                    # first ingestion for a given owner_id bootstraps the record automatically
                    await conn.execute(
                        "INSERT INTO owners (id, username) VALUES ($1, $2) "
                        "ON CONFLICT (id) DO NOTHING",
                        owner_id,
                        "owner_%d" % owner_id,
                    )
                # ON CONFLICT on the partial unique index (content_hash WHERE <> '')
                # returns NULL if the document already exists — we detect and skip it
                doc_id: int | None = await conn.fetchval(
                    "INSERT INTO documents "
                    "(title, content, correspondent_id, document_type_id, owner_id, "
                    "created_date, mime_type, file_name, storage_ref, content_hash) "
                    "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10) "
                    "ON CONFLICT (content_hash) WHERE content_hash <> '' DO NOTHING "
                    "RETURNING id",
                    title or "",
                    "",  # content is populated later via do_update_document — upload phase has no OCR
                    correspondent_id,
                    document_type_id,
                    owner_id,
                    parsed_date,
                    mime_type or "",
                    file_name,
                    storage_ref,
                    content_hash,
                )

                if doc_id is None:
                    # DB has a record with the same content_hash — but the storage file
                    # might have been lost (manual deletion, failed backup restore, etc.)
                    existing_row = await conn.fetchrow(
                        "SELECT id, storage_ref FROM documents WHERE content_hash = $1",
                        content_hash,
                    )
                    existing_id: int = existing_row["id"]
                    existing_storage_ref: str = existing_row["storage_ref"]

                    storage_intact = await self._storage.do_exists(existing_storage_ref)
                    if storage_intact:
                        # true duplicate — storage and DB record both present; signal the
                        # IngestionService to skip further processing for this file
                        self.logging.info(
                            "DMSClientPostgresql: duplicate detected for '%s' "
                            "(content_hash=%s) — existing id=%d",
                            file_name, content_hash[:12], existing_id,
                        )
                        raise FileExistsError(existing_id)

                    # orphaned DB record — storage was lost but the DB entry survived;
                    # do_store() already re-wrote the file, so update the storage_ref and
                    # return the existing id so the IngestionService re-runs OCR + metadata
                    await conn.execute(
                        "UPDATE documents SET storage_ref = $1 WHERE id = $2",
                        storage_ref, existing_id,
                    )
                    self.logging.warning(
                        "DMSClientPostgresql: storage file missing for document id=%d "
                        "(content_hash=%s) — re-linked to '%s', re-processing",
                        existing_id, content_hash[:12], storage_ref,
                    )
                    return existing_id

                if tag_ids:
                    # bulk-insert all tags in the same transaction to stay atomic
                    await conn.executemany(
                        "INSERT INTO document_tags (document_id, tag_id) VALUES ($1, $2) "
                        "ON CONFLICT DO NOTHING",
                        [(doc_id, tid) for tid in tag_ids],
                    )

        self.logging.info(
            "DMSClientPostgresql: uploaded document '%s' → id=%d", file_name, doc_id
        )
        return doc_id

    async def do_delete_document(self, document_id: int) -> bool:
        """Delete a document record and its stored file.

        DB row is deleted first (CASCADE removes document_tags automatically).
        Storage deletion is attempted afterwards — if it fails we log a WARNING
        rather than raising, since the DB is already clean and the orphaned file
        is not a data-integrity issue.
        """
        self._assert_connected()
        async with self._pool.acquire() as conn:  # type: ignore[union-attr]
            storage_ref: str | None = await conn.fetchval(
                "SELECT storage_ref FROM documents WHERE id=$1", document_id
            )
            await conn.execute(
                "DELETE FROM documents WHERE id=$1", document_id
            )

        # attempt to clean up the stored file — failure here is non-fatal because
        # the authoritative DB record is already gone
        if storage_ref:
            try:
                await self._storage.do_delete(storage_ref)
            except Exception as exc:
                self.logging.warning(
                    "DMSClientPostgresql: DB record for document id=%d deleted but "
                    "storage cleanup failed (storage_ref='%s'): %s",
                    document_id, storage_ref, exc,
                )
        return True

    async def do_create_correspondent(self, name: str) -> int:
        """Insert a new correspondent row and return its generated ID."""
        self._assert_connected()
        async with self._pool.acquire() as conn:  # type: ignore[union-attr]
            new_id: int = await conn.fetchval(
                "INSERT INTO correspondents (name) VALUES ($1) RETURNING id", name
            )
        self.logging.info(
            "DMSClientPostgresql: created correspondent '%s' → id=%d", name, new_id
        )
        return new_id

    async def do_create_document_type(self, name: str) -> int:
        """Insert a new document_type row and return its generated ID."""
        self._assert_connected()
        async with self._pool.acquire() as conn:  # type: ignore[union-attr]
            new_id: int = await conn.fetchval(
                "INSERT INTO document_types (name) VALUES ($1) RETURNING id", name
            )
        self.logging.info(
            "DMSClientPostgresql: created document_type '%s' → id=%d", name, new_id
        )
        return new_id

    async def do_create_tag(self, name: str) -> int:
        """Insert a new tag row and return its generated ID."""
        self._assert_connected()
        async with self._pool.acquire() as conn:  # type: ignore[union-attr]
            new_id: int = await conn.fetchval(
                "INSERT INTO tags (name) VALUES ($1) RETURNING id", name
            )
        self.logging.info(
            "DMSClientPostgresql: created tag '%s' → id=%d", name, new_id
        )
        return new_id

    async def do_fetch_custom_fields(self) -> list[CustomFieldBase]:
        """Fetch all custom field definitions from the database."""
        self._assert_connected()
        async with self._pool.acquire() as conn:  # type: ignore[union-attr]
            rows = await conn.fetch(
                "SELECT id, name, data_type FROM custom_fields"
            )
        custom_fields: list[CustomFieldBase] = [
            self._row_to_custom_field_details(row) for row in rows
        ]
        self.logging.info(
            "DMSClientPostgresql: fetched %d custom field(s) from database", len(custom_fields)
        )
        return custom_fields

    def get_custom_fields(self) -> dict[str, CustomFieldDetails]:
        """
        Returns the cached custom field definitions keyed by field id.

        Populated by fill_cache() — safe to call after boot().

        Returns:
            dict[str, CustomFieldDetails]: Custom field definitions indexed by str(id).
        """
        return self._cache_custom_fields

    async def do_create_custom_field(self, name: str, data_type: str = "string") -> int:
        """Insert a new custom field definition row and return its generated ID."""
        self._assert_connected()
        async with self._pool.acquire() as conn:  # type: ignore[union-attr]
            new_id: int = await conn.fetchval(
                "INSERT INTO custom_fields (name, data_type) VALUES ($1, $2) RETURNING id",
                name, data_type,
            )
        self.logging.info(
            "DMSClientPostgresql: created custom_field '%s' (type=%s) → id=%d",
            name, data_type, new_id,
        )
        return new_id

    async def do_update_document(
        self, document_id: int, update: DocumentUpdateRequest
    ) -> bool:
        """Apply a partial metadata update to an existing document.

        Only fields present in the update object (not None) are written so callers
        can change a single field without knowing the current values of all others.
        Tag updates are fully replaced: existing rows are deleted then re-inserted
        in the same transaction to keep the many-to-many table consistent.
        """
        self._assert_connected()

        # build the SET clause dynamically so we never overwrite fields the caller
        # did not intend to touch — None fields are deliberately excluded
        set_parts: list[str] = []
        params: list = []
        idx = 1

        if update.title is not None:
            set_parts.append("title=$%d" % idx)
            params.append(update.title)
            idx += 1
        if update.correspondent_id is not None:
            set_parts.append("correspondent_id=$%d" % idx)
            params.append(update.correspondent_id)
            idx += 1
        if update.document_type_id is not None:
            set_parts.append("document_type_id=$%d" % idx)
            params.append(update.document_type_id)
            idx += 1
        if update.content is not None:
            set_parts.append("content=$%d" % idx)
            params.append(update.content)
            idx += 1
        if update.created_date is not None:
            set_parts.append("created_date=$%d" % idx)
            try:
                params.append(datetime.fromisoformat(update.created_date))
            except ValueError:
                self.logging.warning(
                    "DMSClientPostgresql: could not parse created_date '%s' for update, skipping field",
                    update.created_date,
                )
                set_parts.pop()
                idx -= 1
            else:
                idx += 1
        if update.owner_id is not None:
            set_parts.append("owner_id=$%d" % idx)
            params.append(update.owner_id)
            idx += 1

        # resolve custom field names → IDs before entering the transaction so we don't
        # hold the connection while doing additional async DB round-trips for each field
        resolved_custom_fields: list[tuple[int, str]] = []
        if update.custom_fields:
            for field_name, value in update.custom_fields.items():
                field_id = await self.do_resolve_or_create_custom_field(field_name)
                resolved_custom_fields.append((field_id, value))

        async with self._pool.acquire() as conn:  # type: ignore[union-attr]
            async with conn.transaction():
                if set_parts:
                    params.append(document_id)
                    sql = "UPDATE documents SET %s WHERE id=$%d" % (
                        ", ".join(set_parts), idx
                    )
                    await conn.execute(sql, *params)

                if update.tag_ids is not None:
                    # delete existing associations then re-insert the full desired set —
                    # simpler and safer than diffing the delta for a small tag list
                    await conn.execute(
                        "DELETE FROM document_tags WHERE document_id=$1", document_id
                    )
                    if update.tag_ids:
                        await conn.executemany(
                            "INSERT INTO document_tags (document_id, tag_id) "
                            "VALUES ($1, $2) ON CONFLICT DO NOTHING",
                            [(document_id, tid) for tid in update.tag_ids],
                        )

                if resolved_custom_fields:
                    # replace the entire custom field set atomically — delete then re-insert
                    # is simpler than computing a diff for a typically-small collection
                    await conn.execute(
                        "DELETE FROM document_custom_fields WHERE document_id=$1", document_id
                    )
                    await conn.executemany(
                        "INSERT INTO document_custom_fields (document_id, custom_field_id, value) "
                        "VALUES ($1, $2, $3) ON CONFLICT (document_id, custom_field_id) DO UPDATE SET value=$3",
                        [(document_id, field_id, value) for field_id, value in resolved_custom_fields],
                    )

        self.logging.debug(
            "DMSClientPostgresql: updated document id=%d", document_id
        )
        return True

    ##########################################
    ########### RESPONSE PARSER ##############
    ##########################################

    # ---- The parse_endpoint_* methods are only meaningful for HTTP-based backends.
    #      PostgreSQL bypasses HTTP entirely; all parsing happens via the _row_to_*
    #      helper methods below. These stubs satisfy the abstract contract.

    ############# WRITE RESPONSES ############

    def _parse_endpoint_create_correspondent(self, response: dict) -> int:
        raise NotImplementedError(
            "PostgreSQL backend bypasses HTTP — _parse_endpoint_create_correspondent is overridden directly"
        )

    def _parse_endpoint_create_document_type(self, response: dict) -> int:
        raise NotImplementedError(
            "PostgreSQL backend bypasses HTTP — _parse_endpoint_create_document_type is overridden directly"
        )

    def _parse_endpoint_create_tag(self, response: dict) -> int:
        raise NotImplementedError(
            "PostgreSQL backend bypasses HTTP — _parse_endpoint_create_tag is overridden directly"
        )

    def _parse_endpoint_update_document(self, response: dict) -> bool:
        raise NotImplementedError(
            "PostgreSQL backend bypasses HTTP — _parse_endpoint_update_document is overridden directly"
        )

    ############### LIST RESPONSES ###############

    def _parse_endpoint_documents(
        self, response: dict, requested_page_size: int | None = None
    ) -> DocumentsListResponse:
        raise NotImplementedError(
            "PostgreSQL backend bypasses HTTP — _parse_endpoint_documents is overridden directly"
        )

    def _parse_endpoint_correspondents(
        self, response: dict, requested_page_size: int | None = None
    ) -> CorrespondentsListResponse:
        raise NotImplementedError(
            "PostgreSQL backend bypasses HTTP — _parse_endpoint_correspondents is overridden directly"
        )

    def _parse_endpoint_owners(
        self, response: dict, requested_page_size: int | None = None
    ) -> OwnersListResponse:
        raise NotImplementedError(
            "PostgreSQL backend bypasses HTTP — _parse_endpoint_owners is overridden directly"
        )

    def _parse_endpoint_tags(
        self, response: dict, requested_page_size: int | None = None
    ) -> TagsListResponse:
        raise NotImplementedError(
            "PostgreSQL backend bypasses HTTP — _parse_endpoint_tags is overridden directly"
        )

    def _parse_endpoint_document_types(
        self, response: dict, requested_page_size: int | None = None
    ) -> DocumentTypesListResponse:
        raise NotImplementedError(
            "PostgreSQL backend bypasses HTTP — _parse_endpoint_document_types is overridden directly"
        )

    ############ GET RESPONSES ##############

    def _parse_endpoint_document(self, response: dict) -> DocumentDetails:
        raise NotImplementedError(
            "PostgreSQL backend bypasses HTTP — _parse_endpoint_document is overridden directly"
        )

    def _parse_endpoint_correspondent(self, response: dict) -> CorrespondentDetails:
        raise NotImplementedError(
            "PostgreSQL backend bypasses HTTP — _parse_endpoint_correspondent is overridden directly"
        )

    def _parse_endpoint_owner(self, response: dict) -> OwnerDetails:
        raise NotImplementedError(
            "PostgreSQL backend bypasses HTTP — _parse_endpoint_owner is overridden directly"
        )

    def _parse_endpoint_tag(self, response: dict) -> TagDetails:
        raise NotImplementedError(
            "PostgreSQL backend bypasses HTTP — _parse_endpoint_tag is overridden directly"
        )

    def _parse_endpoint_document_type(self, response: dict) -> DocumentTypeDetails:
        raise NotImplementedError(
            "PostgreSQL backend bypasses HTTP — _parse_endpoint_document_type is overridden directly"
        )

    ############ ENDPOINT STUBS ##############
    # These abstract methods from DMSClientInterface describe HTTP endpoint paths.
    # asyncpg accesses the DB directly, so no endpoint paths are needed.

    def _get_endpoint_documents(self, page: int = 1, page_size: int = 100) -> str:
        raise NotImplementedError(
            "PostgreSQL backend bypasses HTTP — _get_endpoint_documents is not applicable"
        )

    def _get_endpoint_correspondents(self, page: int = 1, page_size: int = 100) -> str:
        raise NotImplementedError(
            "PostgreSQL backend bypasses HTTP — _get_endpoint_correspondents is not applicable"
        )

    def _get_endpoint_tags(self, page: int = 1, page_size: int = 100) -> str:
        raise NotImplementedError(
            "PostgreSQL backend bypasses HTTP — _get_endpoint_tags is not applicable"
        )

    def _get_endpoint_owners(self, page: int = 1, page_size: int = 100) -> str:
        raise NotImplementedError(
            "PostgreSQL backend bypasses HTTP — _get_endpoint_owners is not applicable"
        )

    def _get_endpoint_document_types(self, page: int = 1, page_size: int = 100) -> str:
        raise NotImplementedError(
            "PostgreSQL backend bypasses HTTP — _get_endpoint_document_types is not applicable"
        )

    def _get_endpoint_document_details(self, document_id: str) -> str:
        raise NotImplementedError(
            "PostgreSQL backend bypasses HTTP — _get_endpoint_document_details is not applicable"
        )

    def _get_endpoint_correspondent_details(self, correspondent_id: str) -> str:
        raise NotImplementedError(
            "PostgreSQL backend bypasses HTTP — _get_endpoint_correspondent_details is not applicable"
        )

    def _get_endpoint_tag_details(self, tag_id: str) -> str:
        raise NotImplementedError(
            "PostgreSQL backend bypasses HTTP — _get_endpoint_tag_details is not applicable"
        )

    def _get_endpoint_owner_details(self, owner_id: str) -> str:
        raise NotImplementedError(
            "PostgreSQL backend bypasses HTTP — _get_endpoint_owner_details is not applicable"
        )

    def _get_endpoint_document_type_details(self, document_type_id: str) -> str:
        raise NotImplementedError(
            "PostgreSQL backend bypasses HTTP — _get_endpoint_document_type_details is not applicable"
        )

    def _get_endpoint_create_correspondent(self) -> str:
        raise NotImplementedError(
            "PostgreSQL backend bypasses HTTP — _get_endpoint_create_correspondent is not applicable"
        )

    def _get_endpoint_create_document_type(self) -> str:
        raise NotImplementedError(
            "PostgreSQL backend bypasses HTTP — _get_endpoint_create_document_type is not applicable"
        )

    def _get_endpoint_create_tag(self) -> str:
        raise NotImplementedError(
            "PostgreSQL backend bypasses HTTP — _get_endpoint_create_tag is not applicable"
        )

    def _get_endpoint_update_document(self, document_id: int) -> str:
        raise NotImplementedError(
            "PostgreSQL backend bypasses HTTP — _get_endpoint_update_document is not applicable"
        )

    ############ PAYLOAD STUBS ##############
    # Payload construction is only meaningful for HTTP backends that POST JSON.
    # The PostgreSQL backend constructs SQL directly in do_create_* / do_update_*.

    def get_create_correspondent_payload(self, name: str) -> dict:
        raise NotImplementedError(
            "PostgreSQL backend bypasses HTTP — get_create_correspondent_payload is not applicable"
        )

    def get_create_document_type_payload(self, name: str) -> dict:
        raise NotImplementedError(
            "PostgreSQL backend bypasses HTTP — get_create_document_type_payload is not applicable"
        )

    def get_create_tag_payload(self, name: str) -> dict:
        raise NotImplementedError(
            "PostgreSQL backend bypasses HTTP — get_create_tag_payload is not applicable"
        )

    def get_update_document_payload(
        self, document_id: int, update: DocumentUpdateRequest, custom_field_pairs: list[tuple[int, str]] | None = None
    ) -> dict:
        raise NotImplementedError(
            "PostgreSQL backend bypasses HTTP — get_update_document_payload is not applicable"
        )

    def _get_endpoint_custom_fields(self, page: int, page_size: int) -> str:
        raise NotImplementedError(
            "PostgreSQL backend bypasses HTTP — _get_endpoint_custom_fields is not applicable"
        )

    def _get_endpoint_create_custom_field(self) -> str:
        raise NotImplementedError(
            "PostgreSQL backend bypasses HTTP — _get_endpoint_create_custom_field is not applicable"
        )

    def get_create_custom_field_payload(self, name: str, data_type: str) -> dict:
        raise NotImplementedError(
            "PostgreSQL backend bypasses HTTP — get_create_custom_field_payload is not applicable"
        )

    def _parse_endpoint_custom_fields(self, response: dict) -> CustomFieldsListResponse:
        raise NotImplementedError(
            "PostgreSQL backend bypasses HTTP — _parse_endpoint_custom_fields is not applicable"
        )

    def _parse_endpoint_custom_field(self, response: dict) -> CustomFieldDetails:
        raise NotImplementedError(
            "PostgreSQL backend bypasses HTTP — _parse_endpoint_custom_field is not applicable"
        )

    def _parse_endpoint_create_custom_field(self, response: dict) -> int:
        raise NotImplementedError(
            "PostgreSQL backend bypasses HTTP — _parse_endpoint_create_custom_field is not applicable"
        )

    ##########################################
    ################# CORE ###################
    ##########################################

    async def _ensure_tables(self) -> None:
        """Create all required tables in a single transaction if they do not exist.

        DDL is centralised here rather than in a migration tool so the service can
        be started against a fresh database without manual setup steps. The IF NOT
        EXISTS guard makes this idempotent — safe to call on every boot.
        """
        self._assert_connected()
        async with self._pool.acquire() as conn:  # type: ignore[union-attr]
            async with conn.transaction():
                await conn.execute(self._get_ddl_owners())
                await conn.execute(self._get_ddl_correspondents())
                await conn.execute(self._get_ddl_document_types())
                await conn.execute(self._get_ddl_tags())
                await conn.execute(self._get_ddl_documents())
                await conn.execute(self._get_ddl_document_tags())
                await conn.execute(self._get_ddl_custom_fields())
                await conn.execute(self._get_ddl_document_custom_fields())
                # migration: add content_hash to tables created before the column existed
                await conn.execute(self._get_ddl_content_hash_column())
            # CREATE INDEX must run outside the transaction that creates the table
            await conn.execute(self._get_ddl_content_hash_index())
        self.logging.debug("DMSClientPostgresql: schema ensured")

    ##########################################
    ############# HELPERS ####################
    ##########################################

    ################ DDL GETTERS ##################

    def _get_ddl_owners(self) -> str:
        return (
            "CREATE TABLE IF NOT EXISTS owners ("
            "    id SERIAL PRIMARY KEY,"
            "    username TEXT NOT NULL UNIQUE,"
            "    email TEXT NOT NULL DEFAULT '',"
            "    firstname TEXT NOT NULL DEFAULT '',"
            "    lastname TEXT NOT NULL DEFAULT ''"
            ")"
        )

    def _get_ddl_correspondents(self) -> str:
        return (
            "CREATE TABLE IF NOT EXISTS correspondents ("
            "    id SERIAL PRIMARY KEY,"
            "    name TEXT NOT NULL,"
            "    slug TEXT NOT NULL DEFAULT '',"
            "    owner_id INTEGER REFERENCES owners(id) ON DELETE SET NULL"
            ")"
        )

    def _get_ddl_document_types(self) -> str:
        return (
            "CREATE TABLE IF NOT EXISTS document_types ("
            "    id SERIAL PRIMARY KEY,"
            "    name TEXT NOT NULL,"
            "    slug TEXT NOT NULL DEFAULT '',"
            "    owner_id INTEGER REFERENCES owners(id) ON DELETE SET NULL"
            ")"
        )

    def _get_ddl_tags(self) -> str:
        return (
            "CREATE TABLE IF NOT EXISTS tags ("
            "    id SERIAL PRIMARY KEY,"
            "    name TEXT NOT NULL,"
            "    slug TEXT NOT NULL DEFAULT '',"
            "    owner_id INTEGER REFERENCES owners(id) ON DELETE SET NULL"
            ")"
        )

    def _get_ddl_documents(self) -> str:
        return (
            "CREATE TABLE IF NOT EXISTS documents ("
            "    id SERIAL PRIMARY KEY,"
            "    title TEXT NOT NULL DEFAULT '',"
            "    content TEXT NOT NULL DEFAULT '',"
            "    correspondent_id INTEGER REFERENCES correspondents(id) ON DELETE SET NULL,"
            "    document_type_id INTEGER REFERENCES document_types(id) ON DELETE SET NULL,"
            "    owner_id INTEGER REFERENCES owners(id) ON DELETE SET NULL,"
            "    created_date TIMESTAMPTZ,"
            "    mime_type TEXT NOT NULL DEFAULT '',"
            "    file_name TEXT NOT NULL DEFAULT '',"
            "    storage_ref TEXT NOT NULL DEFAULT '',"
            "    content_hash TEXT NOT NULL DEFAULT '',"
            "    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()"
            ")"
        )

    def _get_ddl_content_hash_column(self) -> str:
        # migration guard: adds the column to tables created before content_hash was introduced
        return "ALTER TABLE documents ADD COLUMN IF NOT EXISTS content_hash TEXT NOT NULL DEFAULT ''"

    def _get_ddl_content_hash_index(self) -> str:
        # partial unique index: only enforces uniqueness for non-empty hashes so that
        # rows without a hash (e.g. migration artifacts with DEFAULT '') do not conflict
        return (
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_documents_content_hash "
            "ON documents(content_hash) WHERE content_hash <> ''"
        )

    def _get_ddl_document_tags(self) -> str:
        return (
            "CREATE TABLE IF NOT EXISTS document_tags ("
            "    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,"
            "    tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,"
            "    PRIMARY KEY (document_id, tag_id)"
            ")"
        )

    def _get_ddl_custom_fields(self) -> str:
        return (
            "CREATE TABLE IF NOT EXISTS custom_fields ("
            "    id SERIAL PRIMARY KEY,"
            "    name TEXT NOT NULL UNIQUE,"
            "    data_type TEXT NOT NULL DEFAULT 'string'"
            ")"
        )

    def _get_ddl_document_custom_fields(self) -> str:
        return (
            "CREATE TABLE IF NOT EXISTS document_custom_fields ("
            "    document_id      INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,"
            "    custom_field_id  INTEGER NOT NULL REFERENCES custom_fields(id) ON DELETE CASCADE,"
            "    value            TEXT NOT NULL DEFAULT '',"
            "    PRIMARY KEY (document_id, custom_field_id)"
            ")"
        )

    ################ ROW MAPPERS ##################

    def _row_to_document_details(
        self, row: asyncpg.Record, tag_ids: list[str], custom_field_ids: dict[str, str] | None = None
    ) -> DocumentDetails:
        """Map an asyncpg Record from the documents table to a DocumentDetails model."""
        return DocumentDetails(
            engine=self._get_engine_name(),
            id=str(row["id"]),
            title=row["title"],
            content=row["content"],
            correspondent_id=str(row["correspondent_id"]) if row["correspondent_id"] else None,
            document_type_id=str(row["document_type_id"]) if row["document_type_id"] else None,
            owner_id=str(row["owner_id"]) if row["owner_id"] else None,
            tag_ids=tag_ids,
            created_date=row["created_date"],
            mime_type=row["mime_type"],
            file_name=row["file_name"],
            # raw {str(field_id): value} pairs — fill_cache() resolves these to field names
            custom_field_ids=custom_field_ids or {},
        )

    def _row_to_custom_field_details(self, row: asyncpg.Record) -> CustomFieldDetails:
        """Map an asyncpg Record from the custom_fields table to a CustomFieldDetails model."""
        return CustomFieldDetails(
            engine=self._get_engine_name(),
            id=str(row["id"]),
            name=row["name"],
        )

    def _row_to_correspondent_details(self, row: asyncpg.Record) -> CorrespondentDetails:
        """Map an asyncpg Record from the correspondents table to a CorrespondentDetails model."""
        return CorrespondentDetails(
            engine=self._get_engine_name(),
            id=str(row["id"]),
            name=row["name"],
            slug=row["slug"] or None,
            owner_id=str(row["owner_id"]) if row["owner_id"] else None,
        )

    def _row_to_tag_details(self, row: asyncpg.Record) -> TagDetails:
        """Map an asyncpg Record from the tags table to a TagDetails model."""
        return TagDetails(
            engine=self._get_engine_name(),
            id=str(row["id"]),
            name=row["name"],
            slug=row["slug"] or None,
            owner_id=str(row["owner_id"]) if row["owner_id"] else None,
        )

    def _row_to_owner_details(self, row: asyncpg.Record) -> OwnerDetails:
        """Map an asyncpg Record from the owners table to an OwnerDetails model."""
        return OwnerDetails(
            engine=self._get_engine_name(),
            id=str(row["id"]),
            username=row["username"],
            email=row["email"] or None,
            firstname=row["firstname"] or None,
            lastname=row["lastname"] or None,
        )

    def _row_to_document_type_details(self, row: asyncpg.Record) -> DocumentTypeDetails:
        """Map an asyncpg Record from the document_types table to a DocumentTypeDetails model."""
        return DocumentTypeDetails(
            engine=self._get_engine_name(),
            id=str(row["id"]),
            name=row["name"],
            slug=row["slug"] or None,
            owner_id=str(row["owner_id"]) if row["owner_id"] else None,
        )

    def _assert_connected(self) -> None:
        """Raise if boot() has not been called yet.

        Every public method that touches the DB calls this guard so failures
        produce a clear message rather than an AttributeError on a None pool.
        """
        if self._pool is None:
            raise RuntimeError(
                "DMSClientPostgresql: pool not initialized. Call boot() first."
            )
