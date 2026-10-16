#!/usr/bin/env python3
"""Live smoke test against a real Nextcloud instance.

Requires the same environment variables as the server (.env):
    NC_URL, NC_USERNAME, NC_APP_PASSWORD

Run from the repo root:
    python scripts/smoke_test.py

Creates and removes a few test objects (folder, file) and reads
rooms/calendars/addressbooks/notes. Does NOT delete anything outside of its
own test fixtures.
"""

import asyncio
import sys
import uuid

sys.path.insert(0, "src")

from nc_mcp.config import Config  # noqa: E402
from nc_mcp.nc_client import NC, NcError  # noqa: E402

PASSED: list[str] = []
FAILED: list[tuple[str, Exception]] = []


async def check(name, action):
    """action: coroutine, or zero-arg callable returning a coroutine/value."""
    try:
        result = action if asyncio.iscoroutine(action) else action()
        if asyncio.iscoroutine(result):
            result = await result
        PASSED.append(name)
        preview = str(result)[:100].replace("\n", " ") if result is not None else "ok"
        print(f"  ✅ {name}: {preview}")
        return result
    except Exception as exc:
        FAILED.append((name, exc))
        print(f"  ❌ {name}: {exc}")
        return None


async def main():
    cfg = Config()
    cfg.validate()
    nc = NC(cfg.nc_url, cfg.nc_username, cfg.nc_app_password)
    run_id = uuid.uuid4().hex[:8]
    print(f"Smoke test {run_id} against {cfg.nc_url} as {cfg.nc_username}\n")

    print("[files]")
    entries = await check("files list root", nc.propfind(nc.dav_file_url_path("/"), depth=1))
    folder = f"/nc-mcp-smoke-{run_id}"
    await nc.dav("MKCOL", nc.dav_file_url_path(folder))
    print(f"  ✅ created folder {folder}")
    file_path = f"{folder}/test.txt"
    put = await nc.dav("PUT", nc.dav_file_url_path(file_path), "hello from nc-mcp smoke test", {"Content-Type": "text/plain"})
    put.raise_for_status()
    print(f"  ✅ wrote {file_path}")
    got = await nc.dav("GET", nc.dav_file_url_path(file_path))
    assert got.text == "hello from nc-mcp smoke test", got.text
    print("  ✅ read back content matches")

    print("[notes]")
    await check("notes list", nc.rest("GET", "/index.php/apps/notes/api/v1/notes"))

    print("[calendar]")
    await check("calendars list", nc.propfind(f"/remote.php/dav/calendars/{nc.username}/", depth=1))

    print("[talk]")
    try:
        rooms = await nc.ocs("GET", "/ocs/v2.php/apps/spreed/api/v4/rooms")
        print(f"  ✅ talk rooms: {[r.get('displayName') for r in rooms][:5]}")
    except NcError:
        rooms = await nc.ocs("GET", "/ocs/v2.php/apps/spreed/api/v4/room")
        print(f"  ✅ talk rooms (singular fallback): {[r.get('displayName') for r in rooms][:5]}")

    print("[contacts]")
    await check("addressbooks list", nc.propfind(nc.dav_addressbook_path(""), depth=1))

    print("[search]")
    await check("unified search", nc.ocs("GET", "/ocs/v2.php/search/providers/files/search", params={"term": "test", "from": 0}))

    # cleanup
    await nc.dav("DELETE", nc.dav_file_url_path(folder))
    print(f"\n🧹 cleaned up {folder}")
    await nc.aclose()

    print(f"\n{len(PASSED)} checks passed, {len(FAILED)} failed")
    if FAILED:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
