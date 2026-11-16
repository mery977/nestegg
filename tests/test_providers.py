"""The real-model path, end to end, against a mock OpenAI-compatible server (the protocol used by OpenAI,
Azure OpenAI v1 and local servers such as Ollama): request shape, JSON protocol, tool loop, verification."""

import importlib.util
import json

import pytest

from nestegg.agent import Assistant
from nestegg.llm import make_llm, parse_json


def test_parse_json_tolerates_code_fences_and_prose():
    assert parse_json('Sure!\n```json\n{"tool": "bank_fees", "args": {}}\n```') == {"tool": "bank_fees", "args": {}}


def test_full_turn_through_an_openai_compatible_server(store, tmp_path, monkeypatch):
    openai = pytest.importorskip("openai")
    # recent openai SDKs ship with httpx2, older ones with httpx: use the one the SDK uses
    httpx = pytest.importorskip("httpx2" if importlib.util.find_spec("httpx2") else "httpx")

    replies = iter(
        [
            {"tool": "bank_fees", "args": {}},
            {"answer": "Vous avez payé 58,00 € de frais bancaires.", "figures": ["F1"]},
        ]
    )
    seen = []

    def handler(req):
        body = json.loads(req.content)
        seen.append((req.url.path, body["model"], body["response_format"], body["messages"][1]["content"]))
        content = json.dumps(next(replies), ensure_ascii=False)
        msg = {"role": "assistant", "content": content}
        return httpx.Response(
            200,
            json={
                "id": "x",
                "object": "chat.completion",
                "created": 0,
                "model": body["model"],
                "choices": [{"index": 0, "finish_reason": "stop", "message": msg}],
            },
        )

    monkeypatch.setenv("NESTEGG_LOCAL_URL", "http://localhost:11434/v1")
    llm = make_llm("local", "mistral-small")
    llm.client = openai.OpenAI(
        base_url="http://localhost:11434/v1",
        api_key="local",
        max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    r = Assistant(store.for_customer("C001"), llm, audit_dir=str(tmp_path)).ask("Mes frais ?")
    assert r.answer == "Vous avez payé 58,00 € de frais bancaires." and r.numbers_verified and r.llm_calls == 2
    assert [s[0] for s in seen] == ["/v1/chat/completions"] * 2
    assert seen[0][1] == "mistral-small" and seen[0][2] == {"type": "json_object"}
    assert '"bank_fees"' in seen[1][3] and "58.0" in seen[1][3]  # the tool result went back to the model
