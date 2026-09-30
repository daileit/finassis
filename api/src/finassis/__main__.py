"""CLI: finassis api | worker | migrate | seed | bootstrap | partitions"""

from __future__ import annotations

import argparse
import asyncio
import sys

from .config import get_settings
from .logging import configure_logging, get_logger

log = get_logger("finassis.cli")


def _migrate() -> None:
    from alembic import command
    from alembic.config import Config

    api_dir = get_settings().api_dir
    cfg = Config(str(api_dir / "alembic.ini"))
    cfg.set_main_option("script_location", str(api_dir / "alembic"))
    command.upgrade(cfg, "head")


async def _startup_tasks(seed: bool = True, bootstrap: bool = True, partitions: bool = True) -> None:
    from .db import Database
    from .domain import bootstrap as bs
    from .domain import seeds

    db = Database()
    await db.connect()
    try:
        if seed:
            await seeds.load_all(db)
        if partitions:
            async with db.admin_conn() as conn:
                await conn.execute("SELECT ensure_partitions($1)", get_settings().load_behaviour().partitions_months_ahead)
        if bootstrap:
            await bs.ensure_admin(db)
    finally:
        await db.close()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="finassis")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("api", help="run migrations, seeds, bootstrap, then serve the API")
    sub.add_parser("worker", help="run background jobs")
    sub.add_parser("migrate", help="alembic upgrade head")
    sub.add_parser("seed", help="load seeds/*.json")
    sub.add_parser("bootstrap", help="ensure the admin user/key exists")
    sub.add_parser("partitions", help="ensure monthly partitions exist")
    args = p.parse_args(argv)

    s = get_settings()
    configure_logging(s.log_level, json=(s.env != "dev"))

    if args.cmd == "migrate":
        _migrate()
    elif args.cmd == "seed":
        asyncio.run(_startup_tasks(seed=True, bootstrap=False, partitions=False))
    elif args.cmd == "bootstrap":
        asyncio.run(_startup_tasks(seed=False, bootstrap=True, partitions=False))
    elif args.cmd == "partitions":
        asyncio.run(_startup_tasks(seed=False, bootstrap=False, partitions=True))
    elif args.cmd == "api":
        import uvicorn

        _migrate()
        asyncio.run(_startup_tasks())
        uvicorn.run("finassis.api.app:create_app", factory=True, host="0.0.0.0", port=8000, log_level=s.log_level.lower())
    elif args.cmd == "worker":
        from .jobs.scheduler import run_worker

        _migrate()
        asyncio.run(_startup_tasks())
        asyncio.run(run_worker())
    return 0


if __name__ == "__main__":
    sys.exit(main())
