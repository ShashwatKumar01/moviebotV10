"""Runner and lifecycle manager for Primary AutoFilter Bot."""

from __future__ import annotations

import asyncio
import logging
import os
from pyrogram import Client

from .config import Config
from .core.database import Database
from .bot.admin_commands import register_admin_commands
from .bot.channel_watcher import register_channel_watcher
from .bot.release_explorer import register_release_explorer
from .bot.broadcast import register_broadcast
from .bot.autofilter import register_autofilter
from .bot.scheduler import daily_scheduler_loop

logger = logging.getLogger("autofilter_bot")


async def start_primary_bot(
    config: Config,
    file_bot_username: str | None = None,
) -> tuple[Client, asyncio.Task, Database]:
    """Start Primary AutoFilter Bot client and background scheduler."""
    logger.info("Starting Primary AutoFilter Bot with token %s...***", config.bot_token[:10])

    # 1. Initialize Database connection
    db = Database(config.database_uri, config.database_name, config.collection_name)
    db.connect()
    await db.ensure_indexes()

    # 2. Session directory
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    data_dir = os.path.join(base_dir, "data")
    os.makedirs(data_dir, exist_ok=True)

    # 3. Create Pyrogram Client (without plugins root to prevent conflict with LazyPrincessBot plugins)
    app = Client(
        name="primary_autofilter_bot",
        api_id=config.api_id,
        api_hash=config.api_hash,
        bot_token=config.bot_token,
        workdir=data_dir,
        sleep_threshold=5,
    )

    # 4. Register modular handlers
    register_admin_commands(app, config, db)
    register_channel_watcher(app, config, db)
    register_release_explorer(app, config, db)
    register_broadcast(app, config, db)
    register_autofilter(app, config, db)

    # 5. Start bot client
    await app.start()
    bot_info = await app.get_me()
    app.me = bot_info
    logger.info("Primary AutoFilter Bot online as @%s (ID: %s)", bot_info.username, bot_info.id)

    # 6. Start daily scheduler
    scheduler_task = asyncio.create_task(daily_scheduler_loop(app, config, db))

    return app, scheduler_task, db


async def stop_primary_bot(app: Client | None, scheduler_task: asyncio.Task | None) -> None:
    """Gracefully stop Primary AutoFilter Bot."""
    if scheduler_task and not scheduler_task.done():
        scheduler_task.cancel()
        logger.info("Primary AutoFilter Bot scheduler stopped.")
    if app and app.is_connected:
        await app.stop()
        logger.info("Primary AutoFilter Bot stopped.")
