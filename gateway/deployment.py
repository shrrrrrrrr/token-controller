"""Explicit preview boundary; this mode never enables model execution."""
from urllib.parse import urlsplit


def validate_public_origin(value):
    try:
        url = urlsplit(value or "")
        port = url.port
    except ValueError:
        raise ValueError("public origin must be an HTTPS origin") from None
    if (url.scheme != "https" or not url.hostname or url.username or url.password
            or url.path not in ("", "/") or url.query or url.fragment
            or any(c in url.netloc for c in "*\\ \t\r\n")
            or url.hostname in ("localhost", "127.0.0.1", "::1")
            or (port is not None and port != 443)):
        raise ValueError("public origin must be one HTTPS hostname on port 443 without credentials or path")
    hostname = url.hostname.encode("idna").decode("ascii").lower()
    return "https://" + hostname, hostname


def preview_blocked(path, method):
    path = path.rstrip("/")
    return (path == "/v1" or path.startswith("/v1/")
            or path in ("/api/qwen/probe", "/api/local/shutdown")
            or (path.startswith("/api/runtime") and method not in ("GET", "HEAD", "OPTIONS")))
