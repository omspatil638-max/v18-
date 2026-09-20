"""
ensure_db.py — wait for PostgreSQL and create the database if it does not exist.

Uses asyncpg (already a backend dependency), so the launcher does NOT need the
PostgreSQL client tools (`psql` / `pg_isready`) on PATH.

    python -m scripts.ensure_db --wait 60 [--with-test-db]

Exit codes: 0 ready, 1 unreachable, 2 reachable but login/permission failed.
"""

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Dict
from urllib.parse import unquote, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import asyncpg  # noqa: E402

from app.core.config import settings  # noqa: E402

TEST_DB_SUFFIX = "_test"


def parse_dsn(url: str) -> Dict[str, object]:
    """Split a SQLAlchemy URL (postgresql+asyncpg://...) into asyncpg connect kwargs."""
    parsed = urlparse(url.replace("+asyncpg", "").replace("+psycopg2", ""))
    return {
        "user": unquote(parsed.username or "postgres"),
        "password": unquote(parsed.password or ""),
        "host": parsed.hostname or "localhost",
        "port": parsed.port or 5432,
        "database": (parsed.path or "/postgres").lstrip("/") or "postgres",
    }


async def _connect(cfg: Dict[str, object], database: str, timeout: float = 5.0):
    return await asyncpg.connect(
        user=cfg["user"], password=cfg["password"], host=cfg["host"],
        port=cfg["port"], database=database, timeout=timeout,
    )


async def wait_for_server(cfg: Dict[str, object], wait_seconds: int) -> None:
    """Block until the server accepts connections, or raise after wait_seconds."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + wait_seconds
    last = ""
    attempt = 0
    while loop.time() < deadline:
        attempt += 1
        try:
            conn = await _connect(cfg, "postgres")
            await conn.close()
            return
        except asyncpg.InvalidCatalogNameError:
            return  # server is up; the 'postgres' database just isn't there
        except (asyncpg.InvalidPasswordError, asyncpg.InsufficientPrivilegeError) as exc:
            raise PermissionError(str(exc)) from exc
        except Exception as exc:  # noqa: BLE001 — server not accepting connections yet
            last = f"{exc.__class__.__name__}: {exc}"
            if attempt == 3:
                print(f"    still waiting for PostgreSQL at {cfg['host']}:{cfg['port']} ...", flush=True)
            await asyncio.sleep(1.5)
    raise TimeoutError(last or "timed out")


async def ensure_database(cfg: Dict[str, object], name: str) -> bool:
    """Create `name` if missing. Returns True if it was created."""
    conn = await _connect(cfg, "postgres")
    try:
        exists = await conn.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", name)
        if exists:
            return False
        # Identifier cannot be parameterised; quote it safely instead.
        await conn.execute(f'CREATE DATABASE "{name.replace(chr(34), chr(34) * 2)}"')
        return True
    finally:
        await conn.close()


async def main_async(args: argparse.Namespace) -> int:
    cfg = parse_dsn(settings.DATABASE_URL)
    target = str(cfg["database"])
    where = f"{cfg['host']}:{cfg['port']}"

    try:
        await wait_for_server(cfg, args.wait)
    except PermissionError as exc:
        print(f"[db] PostgreSQL at {where} refused the login for user '{cfg['user']}'.")
        print(f"     {exc}")
        print("     Check the credentials in DATABASE_URL in backend/.env.")
        return 2
    except TimeoutError as exc:
        print(f"[db] Could not reach PostgreSQL at {where} within {args.wait}s.")
        print(f"     Last error: {exc}")
        return 1

    try:
        created = await ensure_database(cfg, target)
    except Exception as exc:  # noqa: BLE001
        print(f"[db] Connected to {where} but could not create database '{target}': {exc}")
        return 2
    print(f"[db] Database '{target}' {'created' if created else 'ready'} at {where}.")

    if args.with_test_db:
        test_name = target + TEST_DB_SUFFIX
        try:
            created = await ensure_database(cfg, test_name)
            print(f"[db] Test database '{test_name}' {'created' if created else 'ready'}.")
        except Exception as exc:  # noqa: BLE001
            print(f"[db] (optional) test database '{test_name}' unavailable: {exc}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Wait for PostgreSQL and ensure the database exists.")
    parser.add_argument("--wait", type=int, default=60, help="seconds to wait for the server")
    parser.add_argument("--with-test-db", action="store_true", help="also create the pytest database")
    return asyncio.run(main_async(parser.parse_args()))


if __name__ == "__main__":
    sys.exit(main())
