"""Generic DMS Custom Field model — backend-independent."""

from pydantic import BaseModel


class CustomFieldBase(BaseModel):
    """
    Represents a single custom field definition with its identity fields.
    """
    engine: str
    id: str


class CustomFieldDetails(CustomFieldBase):
    """
    Represents a single custom field definition with its display name.
    """
    name: str | None = None


class CustomFieldsListResponse(BaseModel):
    """
    Represents the response from a DMS when fetching a list of custom field definitions.
    """
    engine: str
    custom_fields: list[CustomFieldBase] = []
    currentPage: int
    nextPage: int | None = None
    nextPageId: str | None = None
    overallCount: int
