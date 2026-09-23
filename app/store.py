"""Shared time constants. This used to hold the in-memory Store and then the
fake defaults every new account was seeded with (a made-up score, cash flow,
pending consent and even an active lender grant). Those were removed: a new
account has no score until the borrower uploads a statement, and no lender
has access to anything until the borrower approves a real request.
"""

HOUR_MS = 60 * 60 * 1000
DAY_MS = 24 * HOUR_MS
