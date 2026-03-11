
from services.rag_search.helper.IdentityHelper import IdentityHelper
from server.user_mapping.UserMappingService import UserMappingService
from shared.clients.dms.DMSClientInterface import DMSClientInterface
def get_verified_identity_helper(
    frontend_name: str,
    frontend_user_id: str,
    user_mapping_service: UserMappingService,
    dms_clients: list[DMSClientInterface],
) -> IdentityHelper:
    """
    Build and validate an IdentityHelper
    
    Args:
        frontend_name: The name of the frontend (e.g. "slack", "teams")
        frontend_user_id: The user ID from the frontend (e.g. Slack user ID)
        user_mapping_service: The UserMappingService instance to use for lookups
        dms_clients: List of DMS clients to check for mappings

    Returns:
        An IdentityHelper instance with the mappings for the given frontend and user ID

    Raises:
        Exception: If no mapping is found for the given frontend and user ID in any configured engine
    """
    identity_helper = IdentityHelper(
        user_mapping_service=user_mapping_service,
        dms_clients=dms_clients,
        frontend=frontend_name,
        user_id=frontend_user_id,
    )
    if not identity_helper.has_mappings():
        raise Exception("User %s is not allowed to access documents from frontend '%s'" % (frontend_user_id, frontend_name))
    return identity_helper