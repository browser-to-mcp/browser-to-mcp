"""Opt-in hosted generation using the Responses API; keys never enter task data."""

import os
import re
from time import monotonic

from browser_to_mcp.jobs.models import JobError

from .provider import MAX_PROMPT_BYTES, ModelReply, post_json


class OpenAIProvider:
    name = "openai"

    def __init__(
        self,
        *,
        api_key,
        model,
        input_rate,
        output_rate,
        max_tokens=4096,
        transport=None,
    ):
        if (
            not isinstance(api_key, str)
            or not api_key
            or any(ord(char) <= 32 or ord(char) >= 127 for char in api_key)
            or not isinstance(model, str)
            or not re.fullmatch(r"[a-zA-Z0-9][\w./:-]{0,127}", model)
            or model.startswith("sk-")
            or any(
                type(rate) is not int or not 1 <= rate <= 10**12
                for rate in (input_rate, output_rate)
            )
            or type(max_tokens) is not int
            or not 128 <= max_tokens <= 16384
        ):
            raise JobError("provider_configuration")
        self._api_key, self.model = api_key, model
        self.input_rate, self.output_rate = input_rate, output_rate
        self.max_tokens, self._transport = max_tokens, transport

    @classmethod
    def from_environment(cls):
        try:
            return cls(
                api_key=os.environ["OPENAI_API_KEY"],
                model=os.environ["BROWSER_TO_MCP_OPENAI_MODEL"],
                input_rate=int(os.environ["BROWSER_TO_MCP_INPUT_MICROUSD_PER_MILLION"]),
                output_rate=int(os.environ["BROWSER_TO_MCP_OUTPUT_MICROUSD_PER_MILLION"]),
                max_tokens=int(os.environ.get("BROWSER_TO_MCP_MODEL_MAX_TOKENS", "4096")),
            )
        except (KeyError, ValueError):
            raise JobError("provider_configuration") from None

    async def _post(self, path, payload):
        return await post_json(
            "https://api.openai.com/v1/responses" + path,
            payload,
            headers={"Authorization": "Bearer " + self._api_key},
            transport=self._transport,
        )

    def cost(self, inputs, outputs):
        # Rates are micro-US dollars per million tokens, rounded upwards separately.
        return sum(
            (count * rate + 999999) // 1000000
            for count, rate in (
                (inputs, self.input_rate),
                (outputs, self.output_rate),
            )
        )

    async def prepare(self, prompt, schema):
        if len(prompt.encode()) > MAX_PROMPT_BYTES:
            raise JobError("page_too_large")
        # The prompt already includes the closed proposal schema. JSON mode ensures an
        # object; the normal application schema/policy still validates every field.
        payload = dict(model=self.model, input=prompt, text={"format": {"type": "json_object"}})
        counted = await self._post("/input_tokens", payload)
        tokens = counted.get("input_tokens")
        if (
            counted.get("object") != "response.input_tokens"
            or type(tokens) is not int
            or not 1 <= tokens <= 100000
        ):
            raise JobError("provider_response")
        return OpenAIRequest(self, payload, tokens)


class OpenAIRequest:
    def __init__(self, provider, payload, input_tokens):
        self.provider, self.payload, self.input_tokens = provider, payload, input_tokens
        self.token_limit = input_tokens + provider.max_tokens
        self.cost_microusd = provider.cost(input_tokens, provider.max_tokens)

    async def complete(self, *, token_limit):
        if token_limit != self.token_limit:
            raise JobError("provider_response")
        start = monotonic()
        response = await self.provider._post(
            "",
            {
                **self.payload,
                "max_output_tokens": self.provider.max_tokens,
                "store": False,
            },
        )
        try:
            if response.get("status") != "completed" or response.get("error"):
                raise ValueError
            usage = response["usage"]
            inputs, outputs = usage["input_tokens"], usage["output_tokens"]
            if (
                type(inputs) is not int
                or inputs != self.input_tokens
                or type(outputs) is not int
                or not 0 <= outputs <= self.provider.max_tokens
                or type(usage["total_tokens"]) is not int
                or usage["total_tokens"] != inputs + outputs
            ):
                raise ValueError
            texts = []
            for item in response["output"]:
                if item["type"] == "reasoning":
                    continue
                if item["type"] != "message" or item["role"] != "assistant":
                    raise ValueError
                for part in item["content"]:
                    if part["type"] != "output_text" or not isinstance(part["text"], str):
                        raise ValueError
                    texts.append(part["text"])
            if len(texts) != 1 or len(texts[0].encode()) > 65536:
                raise ValueError
        except (KeyError, TypeError, ValueError):
            raise JobError("provider_response") from None
        return ModelReply(
            texts[0],
            inputs,
            outputs,
            monotonic() - start,
            model=self.provider.model,
            estimated_cost_microusd=self.provider.cost(inputs, outputs),
        )
