"""Shared limiter instance. Lives in its own module (not main.py) so routers
can import it without a circular import back through main."""

from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)
