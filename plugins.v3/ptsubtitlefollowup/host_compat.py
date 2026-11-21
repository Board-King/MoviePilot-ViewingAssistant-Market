"""Reviewed MoviePilot entry points shared by the V2 and V3 installation packages."""
from datetime import datetime
import re
import time

from version import APP_VERSION


IS_V3 = APP_VERSION.startswith("v3.")

if IS_V3:
    from app.sdk.config import settings
    from app.sdk.events import Event, eventmanager
    from app.sdk.logging import logger
    from app.sdk.plugin import _PluginBase
    from app.schemas.types import EventType, MessageType as NotificationType
else:
    from app.core.config import settings
    from app.core.event import Event, eventmanager
    from app.log import logger
    from app.plugins import _PluginBase
    from app.schemas.types import EventType, NotificationType


def get_engine():
    if IS_V3:
        from app.db.engine import get_engine as host_engine
        return host_engine()
    from app.db import Engine
    return Engine


def get_administrator_dependency():
    if IS_V3:
        from app.api.dependencies.auth import get_current_active_superuser
    else:
        from app.db.user_oper import get_current_active_superuser
    return get_current_active_superuser


def download_added_date(download_hash, downloader, connection_factory):
    """V3 may replay a durable event long after its download history was written."""
    if not IS_V3:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() - 60))
    with connection_factory() as connection:
        record = connection.execute(
            "SELECT date FROM downloadhistory WHERE download_hash=? AND downloader IS ? "
            "ORDER BY id DESC LIMIT 1", (download_hash, downloader),
        ).fetchone()
    date = record["date"] if record else None
    if not isinstance(date, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", date):
        raise ValueError("Download event has no matching valid history date")
    datetime.strptime(date, "%Y-%m-%d %H:%M:%S")
    return date


def native_page_action(endpoint):
    """Adapt only parameter-free V3 PageRender actions to its strict envelope."""
    def action() -> dict:
        result = endpoint()
        if not isinstance(result, dict):
            raise ValueError("Invalid native page action response")
        return {"success": result.get("success", True) is True,
                "message": str(result.get("message") or ""), "data": result}
    action.__name__ = endpoint.__name__
    return action
