from fastapi import Request

from server.user_mapping.UserMappingService import UserMappingService
from services.dms_rag_sync.SyncService import SyncService
from services.rag_search.SearchService import SearchService
from shared.clients.dms.DMSClientInterface import DMSClientInterface
from shared.clients.llm.LLMClientInterface import LLMClientInterface
from shared.clients.rag.RAGClientInterface import RAGClientInterface
from shared.helper.HelperConfig import HelperConfig
from services.rag_search.agent.ReActAgent import ReActAgent


def get_sync_service(request: Request) -> SyncService:
    return request.app.state.sync_service


def get_search_service(request: Request) -> SearchService:
    return request.app.state.search_service


def get_dms_clients(request: Request) -> list[DMSClientInterface]:
    return request.app.state.dms_clients


def get_rag_clients(request: Request) -> list[RAGClientInterface]:
    return request.app.state.rag_clients


def get_llm_client(request: Request) -> LLMClientInterface:
    return request.app.state.llm_client


def get_helper_config(request: Request) -> HelperConfig:
    return request.app.state.helper_config


def get_user_mapping_service(request: Request) -> UserMappingService:
    return request.app.state.user_mapping_service


def get_react_agent(request: Request) -> ReActAgent:
    return request.app.state.react_agent