# Shim: re-export Base from session so that all models importing
# `from apps.api.db.base import Base` resolve correctly.
from apps.api.db.session import Base  # noqa: F401
