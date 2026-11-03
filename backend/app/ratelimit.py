from __future__ import annotations
from typing import Optional, Any, Dict, List, Literal, Union, Callable

from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)
