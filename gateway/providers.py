"""The real provider reuses LiteLLM; demo responses never claim model quality."""
import asyncio
import os
import time

from .store import Fault


class DemoText:
    source = "simulation"

    async def complete(self, body):
        await asyncio.sleep(0.05)
        return dict(id="chatcmpl-local-demo", object="chat.completion", created=int(time.time()),
                    model="qwen3-32b", choices=[dict(index=0, message=dict(role="assistant", content="本地网关闭环已贯通。这是模拟回答，未调用Qwen或GPU。"), finish_reason="stop")],
                    usage=dict(prompt_tokens=12, completion_tokens=8, total_tokens=20))


class LiteLLMText:
    source = "qwen_via_litellm"

    def __init__(self, base, api_key):
        from urllib.parse import urlparse
        url = urlparse(base)
        if url.scheme not in ("http", "https") or url.username or url.password or url.query or url.fragment:
            raise ValueError("QWEN_API_BASE must be an HTTP(S) API base without credentials/query")
        if url.hostname not in ("127.0.0.1", "localhost", "::1") and url.scheme != "https":
            raise ValueError("Use a loopback SSH tunnel or verified HTTPS for Qwen")
        self.base, self.api_key = base.rstrip("/"), api_key
        self.verified_at = None

    async def probe(self):
        import httpx
        async with httpx.AsyncClient(timeout=8, follow_redirects=False, trust_env=False) as client:
            headers = {"Authorization": "Bearer " + self.api_key} if self.api_key else {}
            response = await client.get(self.base + "/models", headers=headers)
            if response.status_code != 200:
                raise Fault("qwen_health_or_auth_failed", 503)
            data = response.json()
            if not any(m.get("id") == "qwen3-32b" for m in data.get("data", [])):
                raise Fault("qwen_served_model_not_confirmed", 503)
        self.verified_at = time.time()
        return dict(source="live_read_only_api_probe", model="qwen3-32b", verified_at=self.verified_at)

    async def complete(self, body):
        if not self.verified_at or time.time()-self.verified_at > 300:
            raise Fault("qwen_probe_required_or_stale", 503)
        os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
        import litellm
        litellm.telemetry = False
        litellm.turn_off_message_logging = True
        litellm.suppress_debug_info = True
        # No callbacks, fallback providers, implicit provider selection or retries.
        result = await litellm.acompletion(
            model="openai/qwen3-32b", api_base=self.base, api_key=self.api_key or "local-no-auth",
            messages=body["messages"], max_tokens=body["max_tokens"],
            temperature=body["temperature"], stream=False, timeout=90, num_retries=0,
        )
        return result.model_dump(mode="json")


def verified_usage(response):
    usage = response.get("usage")
    if not isinstance(usage, dict):
        raise Fault("usage_missing_reconciliation", 502)
    values = [usage.get(name) for name in ("prompt_tokens", "completion_tokens", "total_tokens")]
    if any(type(x) is not int or x < 0 for x in values) or values[0]+values[1] != values[2]:
        raise Fault("usage_invalid_reconciliation", 502)
    return values
