"""FastAPI application entry point for dms_ai_bridge Phase III."""

import logging as std_logging
import os
from contextlib import asynccontextmanager
from typing import AsyncGenerator

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from shared.logging.logging_setup import setup_logging
from shared.helper.HelperConfig import HelperConfig
from shared.clients.dms.DMSClientInterface import DMSClientInterface
from shared.clients.rag.RAGClientInterface import RAGClientInterface
from shared.clients.llm.LLMClientInterface import LLMClientInterface
from shared.clients.cache.CacheClientInterface import CacheClientInterface
from shared.clients.cache.CacheClientManager import CacheClientManager
from shared.clients.dms.DMSClientManager import DMSClientManager
from shared.clients.rag.RAGClientManager import RAGClientManager
from shared.clients.llm.LLMClientManager import LLMClientManager
from shared.clients.ClientInterface import ClientInterface
from services.dms_rag_sync.SyncService import SyncService
from services.rag_search.SearchService import SearchService
from server.routers.HealthRouter import router as health_router
from server.routers.WebhookRouter import router as webhook_router
from server.routers.QueryRouter import router as query_router
from server.routers.ChatRouter import router as chat_router
from server.user_mapping.UserMappingService import UserMappingService
from services.agent.AgentService import AgentService

logging = setup_logging()
app_version = os.getenv("APP_VERSION", "unknown")


class _HealthEndpointFilter(std_logging.Filter):
    def filter(self, record: std_logging.LogRecord) -> bool:
        return "/health" not in record.getMessage()


std_logging.getLogger("uvicorn.access").addFilter(_HealthEndpointFilter())


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    # when the app starts
    app.state.logging = logging
    app.state.helper_config = HelperConfig(logger=logging)

    dms_clients = DMSClientManager(helper_config=app.state.helper_config).get_clients()
    rag_clients = RAGClientManager(helper_config=app.state.helper_config).get_clients()
    llm_client = LLMClientManager(helper_config=app.state.helper_config).get_client()
    cache_client = CacheClientManager(helper_config=app.state.helper_config).get_client()
    _clients :list[ClientInterface] = [*dms_clients, *rag_clients, llm_client, cache_client]

    logging.info("Booting all clients...")
    for client in _clients:
        await client.boot()
    app.state.dms_clients = dms_clients
    app.state.rag_clients = rag_clients
    app.state.llm_client = llm_client
    app.state.cache_client = cache_client
    await check_connections(dms_clients, rag_clients, llm_client, cache_client)
    logging.info("All clients booted successfully.", color="green")

    logging.info("Loading all services...")
    app.state.sync_service = SyncService(
        helper_config=app.state.helper_config,
        dms_clients=dms_clients,
        rag_clients=rag_clients,
        embed_client=llm_client,
        cache_client=cache_client,
    )
    app.state.search_service = SearchService(
        helper_config=app.state.helper_config,
        rag_clients=rag_clients,
        llm_client=llm_client,
        cache_client=cache_client,
    )
    app.state.user_mapping_service = UserMappingService()
    app.state.agent_service = AgentService(
        helper_config=app.state.helper_config,
        search_service=app.state.search_service,
        llm_client=llm_client,
    )
    logging.info("All services loaded successfully.", color="green")

    # while the app is running...
    yield

    # when the app shuts down, close all client connections
    logging.info("Shutting down — closing all clients...")
    for client in [*dms_clients, *rag_clients, llm_client]:
        await client.close()
    await cache_client.close()
    logging.info("All clients closed.")


app = FastAPI(
    title="dms_ai_bridge",
    description=(
        "Intelligent middleware between Document Management Systems (e.g. Paperless-ngx) "
        "and AI frontends (OpenWebUI, AnythingLLM) via semantic search. "
        "Documents are indexed into a vector database and served via POST /query. "
        "Incremental sync is triggered via POST /webhook/document."
    ),
    version=app_version,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health_router)
app.include_router(webhook_router)
app.include_router(query_router)
app.include_router(chat_router)


async def check_connections(
    dms_clients: list[DMSClientInterface],
    rag_clients: list[RAGClientInterface],
    llm_client: LLMClientInterface,
    cache_client: CacheClientInterface,
) -> None:
    """Check connectivity to all configured backends on startup.

    DMS and cache failures are non-fatal (sync/search degrade gracefully).
    RAG and LLM failures are fatal — queries cannot be served without them.

    Raises:
        Exception: If a critical service (RAG or LLM) is not reachable.
    """
    for client in dms_clients:
        result = await client.do_healthcheck()
        if not result.is_success:
            raise Exception(
                f"DMS client '{client.get_engine_name()}' is not reachable "
                f"(status {result.status_code}). Cannot serve queries."
            )

    for client in rag_clients:
        result = await client.do_healthcheck()
        if not result.is_success:
            raise Exception(
                f"RAG client '{client.get_engine_name()}' is not reachable "
                f"(status {result.status_code}). Cannot serve queries."
            )

    result = await llm_client.do_healthcheck()
    if not result.is_success:
        raise Exception(
                f"LLM client '{llm_client.get_engine_name()}' is not reachable "
                f"(status {result.status_code}). Cannot serve queries."
            )

    result = await cache_client.do_healthcheck()
    if not result.is_success:
        raise Exception(
                f"Cache client '{cache_client.get_engine_name()}' is not reachable "
                f"(status {result.status_code}). Cannot serve queries."
            )


if __name__ == "__main__":
    import uvicorn

    logging.info(
        "Starting dms_ai_bridge API Server v%s from root dir: %s on port 8000...",
        app_version,
        os.environ.get("ROOT_DIR", "unknown"),
    )
    uvicorn.run(app, host="0.0.0.0", port=8000)
