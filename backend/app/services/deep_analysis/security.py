from __future__ import annotations
from typing import Optional, Any, Dict, List, Literal, Union, Callable

import ipaddress
import socket
from urllib.parse import urlparse
import httpx

ALLOWED_SCHEMES = {"http", "https"}
BLOCKED_PORTS = {21, 22, 23, 25, 53, 110, 135, 139, 445, 3306, 5432, 6379, 27017, 8080, 8443}
PRIVATE_NETWORKS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fe80::/10"),
]

class SSRFValidationError(ValueError):
    pass

def _is_ip_private(ip_str: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_str)
        return any(ip in network for network in PRIVATE_NETWORKS) or not ip.is_global
    except ValueError:
        return True

def validate_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in ALLOWED_SCHEMES:
        raise SSRFValidationError(f"Недопустимая схема: {parsed.scheme}")
    if parsed.username or parsed.password:
        raise SSRFValidationError("URL не должен содержать учетные данные")

    # Проверяем хост раньше порта, чтобы localhost блокировался всегда
    hostname = parsed.hostname or ""
    if hostname.lower() in {"localhost", "localhost.localdomain"}:
        raise SSRFValidationError("Доступ к localhost запрещен")

    if parsed.port and parsed.port in BLOCKED_PORTS:
        raise SSRFValidationError(f"Порт {parsed.port} заблокирован")

    try:
        addr_info = socket.getaddrinfo(hostname, None)
        ips = {info[4][0] for info in addr_info}
        for ip in ips:
            if _is_ip_private(ip):
                raise SSRFValidationError(f"Разрешен доступ только к публичным IP. hostname={hostname} резолвится в {ip}")
    except socket.gaierror:
        raise SSRFValidationError(f"Не удалось разрешить доменное имя: {hostname}")

    return url

class SafeAsyncClient(httpx.AsyncClient):
    """HTTP-клиент, который проверяет каждый редирект на SSRF."""
    async def send(self, request, **kwargs):
        validate_url(str(request.url))
        if kwargs.get("follow_redirects"):
            response = await super().send(request, **kwargs)
            if response.history:
                for resp in response.history:
                    validate_url(str(resp.url))
            return response
        return await super().send(request, **kwargs)
