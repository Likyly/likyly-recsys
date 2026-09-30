"""Official LIKYLY SDK: items, users, events and recommendations."""
from ._ops import RequestOptions
from ._version import __version__
from .client import AsyncLikyly, Likyly
from .errors import (
    ApiError,
    AuthenticationError,
    LikylyError,
    NetworkError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
    RequestTimeoutError,
    ValidationError,
)
from .models import (
    BatchError,
    BatchResult,
    EventBatchResult,
    EventResult,
    Explanation,
    Item,
    ItemList,
    Properties,
    RecommendationResponse,
    RecommendedItem,
    SimilarUser,
    User,
    UserList,
)

__all__ = [
    "Likyly", "AsyncLikyly", "RequestOptions", "__version__",
    "LikylyError", "ApiError", "AuthenticationError", "PermissionDeniedError", "NotFoundError", "RateLimitError",
    "ValidationError", "NetworkError", "RequestTimeoutError",
    "Item", "ItemList", "User", "UserList", "BatchResult", "BatchError", "EventResult", "EventBatchResult",
    "RecommendationResponse", "RecommendedItem", "Explanation", "SimilarUser", "Properties",
]
