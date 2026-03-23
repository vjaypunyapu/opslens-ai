# Re-export the shared settings from apps.api.config so that worker tasks
# can use `from ..config import settings` without modification.
from apps.api.config import get_settings, settings  # noqa: F401
