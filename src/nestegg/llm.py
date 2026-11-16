"""Model providers behind one small interface. The agent never imports a vendor SDK.

    offline    deterministic local planner, no model, no key (tests, demos)
    azure      Azure OpenAI, keyless with a managed identity (DefaultAzureCredential) or an API key
    openai     OpenAI API (OPENAI_API_KEY)
    anthropic  Claude (ANTHROPIC_API_KEY)
    gemini     Gemini (Vertex AI with NESTEGG_GCP_PROJECT, or GEMINI_API_KEY)
    local      any OpenAI-compatible local server, e.g. Ollama with an open model: nothing leaves the machine

Every provider is used the same way: a system prompt and a user message in, a JSON object out.
Retries with exponential backoff on rate limits and server errors are handled here, once.
"""

from __future__ import annotations

import json
import os
import random
import re
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass

DEFAULT_MODELS = {
    "azure": "gpt-5.4-mini",  # deployment name
    "openai": "gpt-5.4-mini",
    "anthropic": "claude-sonnet-5-5",
    "gemini": "gemini-3.8-flash",
    "local": "mistral-small",
}
# Where data goes, shown to the user and written in the audit log.
DATA_LOCATION = {
    "offline": "nowhere: no model is called",
    "local": "this machine only",
    "azure": "your Azure OpenAI resource (your tenant, your region)",
    "openai": "OpenAI API (masked data only)",
    "anthropic": "Anthropic API (masked data only)",
    "gemini": "Google Gemini API (masked data only)",
}


@dataclass
class Usage:
    calls: int = 0
    input_chars: int = 0
    output_chars: int = 0


def _transient(exc: BaseException) -> bool:
    status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    if isinstance(status, int) and (status in {408, 409, 429} or status >= 500):
        return True
    name = type(exc).__name__.lower()
    return any(w in name for w in ("ratelimit", "timeout", "connection", "unavailable", "overloaded", "internalserver"))


class LLM(ABC):
    name = "base"
    max_attempts = 5

    def __init__(self, model: str = ""):
        self.model = model
        self.usage = Usage()
        self._lock = threading.Lock()

    @abstractmethod
    def _complete(self, system: str, user: str) -> str: ...

    def complete_json(self, system: str, user: str) -> dict:
        for attempt in range(1, self.max_attempts + 1):
            try:
                raw = self._complete(system + "\nAnswer with one JSON object only.", user)
                break
            except Exception as e:
                if attempt == self.max_attempts or not _transient(e):
                    raise
                time.sleep(min(30, 2 ** (attempt - 1)) + random.uniform(0, 0.5))
        with self._lock:
            self.usage.calls += 1
            self.usage.input_chars += len(system) + len(user)
            self.usage.output_chars += len(raw)
        return parse_json(raw)


def parse_json(raw: str) -> dict:
    m = re.search(r"```(?:json)?\s*(.*?)```", raw, re.DOTALL)
    text = m.group(1) if m else raw
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"no JSON object in model output: {raw[:200]!r}")
    return json.loads(text[start : end + 1])


class _OpenAICompatible(LLM):
    def __init__(self, client, model: str):
        super().__init__(model)
        self.client = client
        self._temperature = True  # reasoning models refuse a custom temperature: detected, then dropped

    def _complete(self, system: str, user: str) -> str:
        kwargs = {
            "model": self.model,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        if self._temperature:
            try:
                return self.client.chat.completions.create(temperature=0, **kwargs).choices[0].message.content or ""
            except Exception as e:
                if "temperature" not in str(e).lower():
                    raise
                self._temperature = False
        return self.client.chat.completions.create(**kwargs).choices[0].message.content or ""


def make_llm(provider: str | None = None, model: str | None = None) -> LLM:
    provider = provider or os.environ.get("NESTEGG_PROVIDER", "offline")
    model = model or os.environ.get("NESTEGG_MODEL") or DEFAULT_MODELS.get(provider, "")
    if provider == "offline":
        from .offline import OfflinePlanner

        return OfflinePlanner()
    if provider in {"openai", "azure", "local"}:
        from openai import OpenAI

        if provider == "openai":
            client = OpenAI(max_retries=0)
        elif provider == "local":
            base = os.environ.get("NESTEGG_LOCAL_URL", "http://localhost:11434/v1")
            client = OpenAI(base_url=base, api_key="local", max_retries=0)
        else:
            endpoint = os.environ["NESTEGG_AZURE_ENDPOINT"].rstrip("/")
            key = os.environ.get("AZURE_OPENAI_API_KEY")
            if not key:
                from azure.identity import DefaultAzureCredential, get_bearer_token_provider

                key = get_bearer_token_provider(
                    DefaultAzureCredential(), "https://cognitiveservices.azure.com/.default"
                )
            client = OpenAI(base_url=f"{endpoint}/openai/v1/", api_key=key, max_retries=0)
        llm = _OpenAICompatible(client, model)
        llm.name = provider
        return llm
    if provider == "anthropic":
        import anthropic

        class _Claude(LLM):
            name = "anthropic"

            def __init__(self):
                super().__init__(model)
                self.client = anthropic.Anthropic(max_retries=0)

            def _complete(self, system: str, user: str) -> str:
                resp = self.client.messages.create(
                    model=self.model, max_tokens=2048, system=system, messages=[{"role": "user", "content": user}]
                )
                return "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")

        return _Claude()
    if provider == "gemini":
        from google import genai
        from google.genai import types

        project = os.environ.get("NESTEGG_GCP_PROJECT")
        client = (
            genai.Client(vertexai=True, project=project, location=os.environ.get("NESTEGG_GCP_LOCATION", "global"))
            if project
            else genai.Client()
        )

        class _Gemini(LLM):
            name = "gemini"
            vertex = bool(project)

            def _complete(self, system: str, user: str) -> str:
                cfg = types.GenerateContentConfig(system_instruction=system, response_mime_type="application/json")
                return client.models.generate_content(model=self.model, contents=user, config=cfg).text or ""

        return _Gemini(model)
    raise ValueError(f"unknown provider {provider!r}")
