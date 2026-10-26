"""A small provider seam and a loopback-only llama.cpp implementation.

The provider never receives a job store, browser, filename, or publication capability.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from time import monotonic
from typing import Protocol
from urllib.parse import urlsplit

import httpx

from browser_to_mcp.jobs.models import JobError

MAX_RESPONSE_BYTES = 1048576
MAX_PROMPT_BYTES = 131072


async def post_json(url, payload, *, headers=None, transport=None):
    """Bounded provider transport; never expose response bodies or authentication errors."""
    try:
        async with httpx.AsyncClient(
            timeout=60, follow_redirects=False, trust_env=False, transport=transport
        ) as client:
            async with client.stream("POST", url, json=payload, headers=headers) as response:
                if response.status_code != 200:
                    raise JobError("provider_unavailable")
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > MAX_RESPONSE_BYTES:
                        raise JobError("provider_response")
                value = json.loads(data)
                if not isinstance(value, dict):
                    raise ValueError
                return value
    except JobError:
        raise
    except (httpx.HTTPError, ValueError, TypeError, RecursionError):
        raise JobError("provider_unavailable") from None


@dataclass(frozen=True)
class ModelReply:
    content: str
    prompt_tokens: int
    completion_tokens: int
    seconds: float
    model: str | None = None
    estimated_cost_microusd: int | None = None


class PreparedRequest(Protocol):
    token_limit: int
    cost_microusd: int

    async def complete(self, *, token_limit: int) -> ModelReply: ...


class DraftProvider(Protocol):
    async def prepare(self, prompt: str, schema: dict) -> PreparedRequest: ...


class LlamaCppProvider:
    """Use an explicitly configured local model; no download, retry, redirect, or proxy."""

    name = "llama_cpp"

    def __init__(self, endpoint: str, *, api_key=None, max_tokens=4096, transport=None):
        try:
            url = urlsplit(endpoint)
            if (
                url.scheme != "http"
                or url.hostname != "127.0.0.1"
                or not url.port
                or url.username
                or url.password
                or url.path not in {"", "/"}
                or url.query
                or url.fragment
                or type(max_tokens) is not int
                or not 128 <= max_tokens <= 16384
            ):
                raise ValueError
            if api_key is not None and (not api_key or any(ord(c) < 33 for c in api_key)):
                raise ValueError
        except (TypeError, ValueError):
            raise JobError("provider_configuration") from None
        self.endpoint = endpoint.rstrip("/")
        self._api_key = api_key
        self.max_tokens = max_tokens
        self._transport = transport

    @classmethod
    def from_environment(cls):
        try:
            return cls(
                os.environ["BROWSER_TO_MCP_MODEL_URL"],
                api_key=os.environ.get("BROWSER_TO_MCP_MODEL_API_KEY"),
                max_tokens=int(os.environ.get("BROWSER_TO_MCP_MODEL_MAX_TOKENS", "4096")),
            )
        except (KeyError, ValueError):
            raise JobError("provider_configuration") from None

    async def _post(self, path, payload):
        headers = {"Authorization": "Bearer " + self._api_key} if self._api_key else {}
        return await post_json(
            self.endpoint + path,
            payload,
            headers=headers,
            transport=self._transport,
        )

    async def prepare(self, prompt, schema):
        if len(prompt.encode()) > MAX_PROMPT_BYTES:
            raise JobError("page_too_large")
        # Apply the local model's chat template without inference, then tokenize it.
        # Passing exact token IDs to /completion avoids estimating prompt token usage.
        rendered = await self._post(
            "/apply-template",
            {
                "messages": [{"role": "user", "content": prompt}],
            },
        )
        text = rendered.get("prompt")
        if not isinstance(text, str) or len(text.encode()) > MAX_PROMPT_BYTES:
            raise JobError("provider_response")
        result = await self._post(
            "/tokenize",
            {
                "content": text,
                "add_special": True,
                "parse_special": True,
                "with_pieces": False,
            },
        )
        tokens = result.get("tokens")
        if (
            not isinstance(tokens, list)
            or not 1 <= len(tokens) <= 100000
            or any(type(token) is not int or token < 0 for token in tokens)
        ):
            raise JobError("provider_response")
        return LlamaRequest(self, tokens, schema)


class LlamaRequest:
    cost_microusd = 0  # local inference; not a claim that hardware/electricity is free

    def __init__(self, provider, tokens, schema):
        self.provider, self.tokens, self.schema = provider, tokens, schema
        self.token_limit = len(tokens) + provider.max_tokens

    async def complete(self, *, token_limit):
        if token_limit != self.token_limit:
            raise JobError("provider_response")
        start = monotonic()
        response = await self.provider._post(
            "/completion",
            {
                "prompt": self.tokens,
                "n_predict": self.provider.max_tokens,
                "json_schema": self.schema,
                "temperature": 0,
                "stream": False,
                "cache_prompt": False,
            },
        )
        content = response.get("content")
        predicted = response.get("tokens_predicted")
        evaluated = response.get("tokens_evaluated")
        if (
            not isinstance(content, str)
            or len(content.encode()) > 65536
            or type(predicted) is not int
            or not 0 <= predicted <= self.provider.max_tokens
            or type(evaluated) is not int
            or evaluated != len(self.tokens)
            or response.get("truncated") is not False
            or response.get("stop_type") != "eos"
        ):
            raise JobError("provider_response")
        return ModelReply(content, evaluated, predicted, monotonic() - start)


def provider_from_environment():
    choice = os.environ.get("BROWSER_TO_MCP_PROVIDER")
    if choice == "llama_cpp" or choice is None and os.environ.get("BROWSER_TO_MCP_MODEL_URL"):
        return LlamaCppProvider.from_environment()
    if choice == "openai":
        from .cloud import OpenAIProvider

        return OpenAIProvider.from_environment()
    raise JobError("provider_configuration")
