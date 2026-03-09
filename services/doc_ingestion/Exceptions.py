
class PathTemplateValidationError(Exception):
    """
    Raised when a path template has syntax errors or is missing required placeholders, making it impossible to extract metadata from document paths.
    """
class DocumentValidationError(Exception):
    """
    Raised when a document cannot be ingested due to a known, expected condition.
    """
class DocumentPathValidationError(Exception):
    """
    Raised when the path of an document does not fit the minimum requirements for metadata extraction, e.g. missing correspondent.
    
    Callers should log this as WARNING, not ERROR.
    """