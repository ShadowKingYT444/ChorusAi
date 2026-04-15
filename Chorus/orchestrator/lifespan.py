from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from orchestrator.db import ChorusDB

logger = logging.getLogger("orchestrator.lifespan")


@asynccontextmanager
async def lifespan(app):
    db = ChorusDB()
    path = os.getenv("CHORUS_DB_PATH", "./chorus.db")
    try:
        await db.connect(path)
        logger.info("ChorusDB connected at %s", path)
    except Exception as exc:  # noqa: BLE001
        logger.warning("ChorusDB connect failed (%s): continuing without persistence", exc)

    app.state.db = db

    # Inject into module-level singletons if they support attach_db.
    try:
        from orchestrator.main import job_store, registry  # local import to avoid cycles

        if hasattr(job_store, "attach_db"):
            job_store.attach_db(db)
        if hasattr(registry, "attach_db"):
            registry.attach_db(db)
    except Exception as exc:  # noqa: BLE001
        logger.warning("attach_db injection skipped: %s", exc)

    try:
        yield
    finally:
        try:
            await db.close()
        except Exception as exc:  # noqa: BLE001
            logger.warning("db.close failed: %s", exc)
