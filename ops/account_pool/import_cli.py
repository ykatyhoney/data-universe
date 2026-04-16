"""CLI: import a cookie-authenticated account into the pool.

Usage::

    python -m account_pool.import_cli /path/to/account.json
    python -m account_pool.import_cli --stdin < account.json

JSON payload (minimum)::

    {
        "source": "x",
        "user_agent": "Mozilla/5.0 ...",
        "cookies": [
            {"name": "auth_token", "value": "...", "domain": ".x.com"}
        ],
        "pinned_proxy_id": null,
        "notes": "acquired 2026-04-16; aged 7 days"
    }

The file never leaves this process — cookies are sealed with
``OPS_ACCOUNT_POOL_KEY`` before reaching SQL. Recommended: delete the JSON
file after import.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from account_pool.crypto import CookieSealer
from account_pool.schemas import AccountImport
from account_pool.service import AccountAlreadyImported, AccountPoolService
from shared.logging import configure_logging, get_logger

configure_logging()
log = get_logger(__name__)


def _read_payload(args: argparse.Namespace) -> dict[str, object]:
    if args.stdin:
        return dict(json.load(sys.stdin))
    path = Path(args.file)
    return dict(json.loads(path.read_text(encoding="utf-8")))


async def _run(args: argparse.Namespace) -> int:
    payload = _read_payload(args)
    import_req = AccountImport.model_validate(payload)

    sealer = CookieSealer.from_env()
    service = AccountPoolService(sealer=sealer, proxy_pool=None)
    try:
        account_id = await service.import_account(import_req)
    except AccountAlreadyImported as e:
        print(f"import: FAILED (already imported) — {e}", file=sys.stderr)
        return 2

    print(f"import: ok (account_id={account_id}, source={import_req.source})")
    log.info("account_pool.cli_imported", account_id=account_id, source=import_req.source)
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(prog="account_pool.import_cli")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("file", nargs="?", help="path to JSON payload")
    group.add_argument("--stdin", action="store_true", help="read payload from stdin")
    args = parser.parse_args()
    sys.exit(asyncio.run(_run(args)))


if __name__ == "__main__":
    main()
