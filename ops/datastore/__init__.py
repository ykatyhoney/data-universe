"""Persistence layer for the ops stack.

- ``models.py``  : SQLAlchemy 2.x async declarative models (Postgres).
- ``repositories.py`` : thin repository wrappers — no raw SQL in services.
- ``migrations/`` : Alembic, sourced from the models above.

Scraped data still lives in the existing ``storage/miner.sqlite``; the bridge
into it lands in M2.5.
"""
