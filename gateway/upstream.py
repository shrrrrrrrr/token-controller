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
from .timeouts import GENERATION_SECONDS

MAX_RESPONSE = 2 * 1024 * 1024
MAX_CONTEXT_INPUT = 256 * 1024
CONTEXT_TOKENS = 16384
CONTEXT_MARGIN = 512
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

    def __init__(self, config, state, *, transport=None, native_context=False):
        if type(native_context) is not bool:raise ValueError('native context must be explicit boolean')
        self.native_context = native_context
        override=Path(state)/'runtime-route.local.json'
        if override.exists():
            routed=load_upstream_config(override)
            if routed['base_url']!=config['base_url']:
                raise ValueError('runtime override may not change the fixed upstream endpoint')
            config=routed
        self.config = dict(config)
        self.alias = config["alias"]
        self.key_path = Path(state) / "upstream-api.key"
        self.profile = "cloud-text-v1"
        self.profile_hash = hashlib.sha256(canonical(config).encode()).hexdigest()
        if native_context:
            self.profile = 'cloud-text-native-context-v1'
            self.profile_hash = hashlib.sha256(canonical({'route':config,'context_tokens':CONTEXT_TOKENS,
                'context_margin':CONTEXT_MARGIN,'native_context':True}).encode()).hexdigest()
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
            async with asyncio.timeout(12 if method == "GET" else GENERATION_SECONDS):
                async with httpx.AsyncClient(timeout=httpx.Timeout(8 if method == "GET" else GENERATION_SECONDS, connect=4), trust_env=False,
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

    def inference_payload(self, body, *, stream=False):
        payload = dict(body)
        payload.pop('stream_options',None)
        payload.update(model=self.config['served_model'],stream=stream)
        if stream:payload['stream_options']={'include_usage':True}
        return payload

    async def _context_request(self, path, body):
        """Fixed native metadata endpoints, never a caller-supplied URL or retry."""
        parsed=urlsplit(self.config['base_url'])
        if (path not in ('/apply-template','/tokenize') or parsed.scheme!='http' or parsed.hostname!='127.0.0.1'
                or not parsed.port or parsed.path!='/v1' or parsed.username or parsed.password or parsed.query or parsed.fragment):
            raise Fault('context_budget_unavailable',503)
        try:
            async with asyncio.timeout(12):
                async with httpx.AsyncClient(timeout=httpx.Timeout(12,connect=4),trust_env=False,
                        follow_redirects=False,transport=self.transport) as client:
                    async with client.stream('POST',self.config['base_url'][:-3]+path,json=body,
                            headers={'Authorization':'Bearer '+self.secret(),'Accept-Encoding':'identity'}) as response:
                        if response.status_code!=200 or response.headers.get('content-encoding','identity')!='identity':
                            raise Fault('context_budget_unavailable',503)
                        raw=bytearray()
                        async for chunk in response.aiter_bytes():
                            raw.extend(chunk)
                            if len(raw)>MAX_RESPONSE:raise Fault('context_budget_unavailable',503)
                        document=json.loads(raw)
                        if not isinstance(document,dict):raise ValueError()
                        return document
        except Fault:raise
        except (httpx.HTTPError,TimeoutError,ValueError):
            raise Fault('context_budget_unavailable',503) from None

    async def context_points(self, body):
        if not self.native_context:raise Fault('context_budget_unavailable',503)
        if len(canonical(body).encode())>MAX_CONTEXT_INPUT:raise Fault('request_too_large',413)
        await self.probe(cached=True)
        # Use the same normalized generation fields: tools, history and reasoning
        # must reach the renderer too. No prompt or token-list cache is persisted.
        rendered=await self._context_request('/apply-template',self.inference_payload(body))
        prompt=rendered.get('prompt')
        if not isinstance(prompt,str) or not prompt or len(prompt.encode())>2*MAX_CONTEXT_INPUT:
            raise Fault('context_budget_unavailable',503)
        document=await self._context_request('/tokenize',{'content':prompt,'add_special':False,'parse_special':True})
        tokens=document.get('tokens')
        if (not isinstance(tokens,list) or not tokens or len(tokens)>MAX_CONTEXT_INPUT
                or any(type(value) is not int or value<0 for value in tokens)):
            raise Fault('context_budget_unavailable',503)
        points=len(tokens)+body['max_tokens']+CONTEXT_MARGIN
        if points>CONTEXT_TOKENS:raise Fault('context_length_exceeded',400)
        return points

    async def complete(self, body):
        from .protocols import validate_completion
        payload = self.inference_payload(body)
        result = await self._request("POST", "/chat/completions", self.secret(), payload)
        result=validate_completion(result,self.config['served_model'],body['max_tokens'])
        result['model']=self.alias
        return result

    async def stream(self, body):
        """Relay bounded real SSE; final usage is required before settlement."""
        payload=self.inference_payload(body,stream=True)
        try:
            async with asyncio.timeout(GENERATION_SECONDS):
                async with httpx.AsyncClient(timeout=httpx.Timeout(GENERATION_SECONDS,connect=4),trust_env=False,
                        follow_redirects=False,transport=self.transport) as client:
                    async with client.stream('POST',self.config['base_url']+'/chat/completions',
                            json=payload,headers={'Authorization':'Bearer '+self.secret(),'Accept-Encoding':'identity'}) as response:
                        if response.status_code!=200 or not response.headers.get('content-type','').startswith('text/event-stream'):
                            raise Fault('upstream_stream_invalid',502)
                        if response.headers.get('content-encoding','identity')!='identity':
                            raise Fault('upstream_response_encoding_not_supported',502)
                        size=0;buffer=b'';data=[];done=False
                        async for raw in response.aiter_bytes():
                            size+=len(raw);buffer+=raw
                            if size>MAX_RESPONSE:raise Fault('upstream_response_too_large',502)
                            while b'\n' in buffer:
                                line,buffer=buffer.split(b'\n',1);line=line.rstrip(b'\r')
                                if line.startswith(b'data:'):data.append(line[5:].lstrip())
                                elif not line and data:
                                    encoded=b'\n'.join(data);data=[]
                                    if encoded==b'[DONE]':done=True;break
                                    chunk=json.loads(encoded)
                                    if not isinstance(chunk,dict) or chunk.get('model')!=self.config['served_model']:
                                        raise Fault('upstream_response_model_mismatch',502)
                                    chunk['model']=self.alias
                                    yield chunk
                            if done:break
                        if not done:raise Fault('upstream_stream_incomplete',502)
        except Fault:raise
        except (httpx.HTTPError,TimeoutError,ValueError,UnicodeError):
            self.verified_at=0
            raise Fault('upstream_outcome_unknown',502) from None
