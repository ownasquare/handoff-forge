"""Provider-neutral generation adapters.

Cloud SDKs are imported only when an explicitly enabled adapter performs a
request. Importing this package never initializes a client or opens a network
connection.
"""

from handoff_forge.providers.base import (
    ProviderExecutionError,
    ProviderProtocol,
    ProviderStatus,
)
from handoff_forge.providers.offline import OfflineProvider
from handoff_forge.providers.registry import (
    ProviderRegistry,
    ProviderRouter,
    build_default_registry,
)

__all__ = [
    "OfflineProvider",
    "ProviderExecutionError",
    "ProviderProtocol",
    "ProviderRegistry",
    "ProviderRouter",
    "ProviderStatus",
    "build_default_registry",
]
