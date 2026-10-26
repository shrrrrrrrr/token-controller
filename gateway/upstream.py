"""Fixed loopback OpenAI transport for the restricted cloud pilot.

No SDK/provider discovery, environment proxy, redirect, callback or retry. The
operator supplies routing metadata; only an administrator may provision the key.
"""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import time
from urllib.parse import urlsplit

import httpx

from .store import Fault, canonical
from .providers import verified_usage

MAX_RESPONSE = 2 * 1024 * 1024
KEY = re.compile(r"[A-Za-z0-9_.-]{24,256}\Z")
MODEL = re.compile(r"[a-z0-9][a-z0-9._-]{0,95}\Z")


def load_upstream_config(path):
    try:
        raw = Path(path).read_bytes()
        if len(raw) > 4096:
            raise ValueError
        config = json.loads(raw)
        if not isinstance(config, dict) or set(config) != {"alias", "served_model", "base_url"}:
            raise ValueError
        if not isinstance(config["alias"], str) or not MODEL.fullmatch(config["alias"]):
            raise ValueError
        if not isinstance(config["served_model"], str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,159}", config["served_model"]):
            raise ValueError
        url = urlsplit(config["base_url"])
        if (url.scheme != "http" or url.hostname != "127.0.0.1" or not url.port
                or url.username or url.password or url.path != "/v1" or url.query or url.fragment):
            raise ValueError
        return config
    except (OSError, ValueError, TypeError, KeyError):
        raise ValueError("cloud upstream configuration must select one fixed IPv4 loopback /v1 endpoint") from None


class CloudText:
    source = "cloud_loopback_openai"

    def __init__(self, config, state, *, transport=None):
        self.config = dict(config)
        self.alias = config["alias"]
        self.key_path = Path(state) / "upstream-api.key"
        self.profile = "cloud-text-v1"
        self.profile_hash = hashlib.sha256(canonical(config).encode()).hexdigest()
        self.verified_at = 0
        self.rotating = False
        self.transport = transport
        self.probe_lock = asyncio.Lock()

    @property
    def ready(self):
        return bool(self.verified_at and time.time() - self.verified_at < 60)

    @staticmethod
    def validate_key(value):
        if not isinstance(value, str) or not KEY.fullmatch(value):
            raise Fault("invalid_upstream_credential", 400)
        return value

    def secret(self):
        try:
            fd = os.open(self.key_path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            with os.fdopen(fd, "rb") as stream:
                item = os.fstat(stream.fileno())
                if not stat.S_ISREG(item.st_mode) or item.st_nlink != 1 or item.st_size > 1024:
                    raise ValueError
                if os.name != "nt" and (item.st_uid != os.geteuid() or stat.S_IMODE(item.st_mode) != 0o600):
                    raise ValueError
                value = stream.read(1025).decode("ascii").strip()
            return self.validate_key(value)
        except (OSError, ValueError, UnicodeError, Fault):
            self.verified_at = 0
            raise Fault("upstream_credential_not_configured", 503) from None

    async def _request(self, method, path, secret, body=None):
        try:
            async with asyncio.timeout(12 if method == "GET" else 150):
                async with httpx.AsyncClient(timeout=httpx.Timeout(8 if method == "GET" else 120, connect=4), trust_env=False,
                                             follow_redirects=False, transport=self.transport) as client:
                    async with client.stream(method, self.config["base_url"] + path,
                            headers={"Authorization": "Bearer " + secret, "Accept-Encoding": "identity"}, json=body) as response:
                        if response.status_code != 200:
                            self.verified_at = 0
                            raise Fault("upstream_request_failed", 502)
                        if response.headers.get("content-encoding", "identity") != "identity":
                            raise Fault("upstream_response_encoding_not_supported", 502)
                        raw = bytearray()
                        async for chunk in response.aiter_bytes():
                            raw.extend(chunk)
                            if len(raw) > MAX_RESPONSE:
                                raise Fault("upstream_response_too_large", 502)
                        document = json.loads(raw)
                        if not isinstance(document, dict):
                            raise ValueError
                        return document
        except Fault:
            raise
        except (httpx.HTTPError, TimeoutError, ValueError):
            self.verified_at = 0
            raise Fault("upstream_outcome_unknown", 502) from None

    async def probe(self, candidate=None, *, cached=False):
        async with self.probe_lock:
            if cached and candidate is None and self.ready:
                return {"model": self.alias, "source": "live_read_only_api_probe", "verified": True}
            secret = self.validate_key(candidate) if candidate is not None else self.secret()
            result = await self._request("GET", "/models", secret)
            if not isinstance(result.get("data"), list) or not any(isinstance(item, dict)
                    and item.get("id") == self.config["served_model"] for item in result["data"]):
                self.verified_at = 0
                raise Fault("upstream_model_identity_mismatch", 503)
            if candidate is None:
                self.verified_at = time.time()
            return {"model": self.alias, "source": "live_read_only_api_probe", "verified": True}

    async def provision(self, value):
        value = self.validate_key(value)
        await self.probe(value)  # No secret is committed unless upstream identity/auth pass.
        fd, name = tempfile.mkstemp(prefix=".upstream-key-", dir=self.key_path.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                if os.name == "nt":
                    os.chmod(name, 0o600)
                else:
                    os.fchmod(stream.fileno(), 0o600)
                stream.write(value.encode("ascii") + b"\n")
                stream.flush()
                os.fsync(stream.fileno())
            if self.key_path.is_symlink():
                raise Fault("upstream_credential_path_invalid", 503)
            os.replace(name, self.key_path)
            self.verified_at = time.time()
        finally:
            if os.path.exists(name):
                os.unlink(name)

    async def complete(self, body):
        payload = {name: body[name] for name in ("messages", "max_tokens", "temperature")}
        payload.update(model=self.config["served_model"], stream=False)
        result = await self._request("POST", "/chat/completions", self.secret(), payload)
        if result.get("model") != self.config["served_model"]:
            raise Fault("upstream_response_model_mismatch", 502)
        choices = result.get("choices")
        if not isinstance(choices, list) or len(choices) != 1:
            raise Fault("upstream_response_invalid", 502)
        choice = choices[0]
        message = choice.get("message") if isinstance(choice, dict) else None
        if (not isinstance(message, dict) or message.get("role") != "assistant"
                or not isinstance(message.get("content"), str) or message.get("tool_calls")
                or choice.get("finish_reason") not in ("stop", "length")):
            raise Fault("upstream_response_invalid", 502)
        prompt, completion, total = verified_usage(result)
        return {"id": str(result.get("id", ""))[:160], "object": "chat.completion",
                "created": int(time.time()), "model": self.alias,
                "choices": [{"index": 0, "message": {"role": "assistant", "content": message["content"]},
                             "finish_reason": choice["finish_reason"]}],
                "usage": {"prompt_tokens": prompt, "completion_tokens": completion, "total_tokens": total}}
