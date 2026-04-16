"""Account pool service (M4).

Cookie-authenticated accounts (X primary, Reddit secondary) with encrypted-
at-rest cookies, per-account rate budgets, optional proxy pinning, and a
state machine that auto-cools on auth failures.
"""
