"""Normalizer — per-source transformations from raw scraper JSON into the
shape that the SN13 validator's own scraper would emit. Reddit lives in
``reddit.py`` (M7); X lands in M6; the protocol + passthrough fixture
(M2.5) remain in ``base.py`` for integration tests.
"""

from .reddit import RedditNormalizeError, RedditNormalizer

__all__ = ["RedditNormalizeError", "RedditNormalizer"]
