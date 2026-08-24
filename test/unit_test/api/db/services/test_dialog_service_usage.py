#
#  Copyright 2026 The InfiniFlow Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#
"""
Tests for Issue 90: async_chat() final answers carry structured token usage.

decorate_answer() in dialog_service.py now returns
    usage = {"input": used_token_count, "output": tk_num}
where input comes from message_fit_in() and output from
num_tokens_from_string() (RAGFlow local counting, not vendor billing usage).

The field is purely additive: intermediate delta events and the
empty_response shortcut do not carry it, and structure_answer() passes the
answer dict through unchanged so chatbot completions (stream SSE final frame
and non-stream result) expose it without further edits.
"""

import asyncio
import sys
import types
import warnings
from copy import deepcopy
from types import SimpleNamespace

import pytest

warnings.filterwarnings(
    "ignore",
    message="pkg_resources is deprecated as an API.*",
    category=UserWarning,
)


def _install_cv2_stub_if_unavailable():
    try:
        import cv2  # noqa: F401
        return
    except Exception:
        pass
    stub = types.ModuleType("cv2")
    stub.INTER_LINEAR = 1
    stub.INTER_CUBIC = 2
    stub.BORDER_CONSTANT = 0
    stub.BORDER_REPLICATE = 1
    stub.COLOR_BGR2RGB = 0
    stub.COLOR_BGR2GRAY = 1
    stub.COLOR_GRAY2BGR = 2
    stub.IMREAD_IGNORE_ORIENTATION = 128
    stub.IMREAD_COLOR = 1
    stub.RETR_LIST = 1
    stub.CHAIN_APPROX_SIMPLE = 2

    def _module_getattr(name):
        if name.isupper():
            return 0
        raise RuntimeError(f"cv2.{name} is unavailable in this test environment")

    stub.__getattr__ = _module_getattr
    sys.modules["cv2"] = stub


_install_cv2_stub_if_unavailable()

from api.db.services import conversation_service, dialog_service  # noqa: E402


# ---------------------------------------------------------------------------
# Shared stubs (same pattern as test_dialog_service_final_answer.py)
# ---------------------------------------------------------------------------

_KBINFOS = {
    "chunks": [
        {
            "doc_id": "doc-1",
            "content_ltks": "ragflow is a rag engine",
            "content_with_weight": "RAGFlow is a RAG engine.",
            "vector": [0.1, 0.2, 0.3],
            "docnm_kwd": "intro.pdf",
        },
    ],
    "doc_aggs": [{"doc_id": "doc-1", "doc_name": "intro.pdf", "count": 1}],
    "total": 1,
}

_KB = SimpleNamespace(
    id="kb-1",
    embd_id="text-embedding-ada-002@OpenAI",
    tenant_embd_id="text-embedding-ada-002@OpenAI",
    tenant_id="tenant-1",
    chunk_num=1,
    name="Test KB",
    parser_id="general",
)

_LLM_CONFIG = {
    "llm_name": "gpt-4o",
    "llm_factory": "OpenAI",
    "model_type": "chat",
    "max_tokens": 8192,
}

# Deterministic input-token count returned by the message_fit_in stub.
_STUB_INPUT_TOKENS = 111


class _StreamingChatModel:
    """Yields a single-chunk full answer, no citations."""

    def __init__(self, answer: str):
        self.answer = answer
        self.max_length = 8192

    async def async_chat_streamly_delta(self, system_prompt, messages, gen_conf, **_kwargs):
        yield self.answer

    async def async_chat(self, system_prompt, messages, gen_conf, **_kwargs):
        return self.answer


class _StubRetriever:
    async def retrieval(self, *_args, **_kwargs):
        return deepcopy(_KBINFOS)

    def retrieval_by_children(self, chunks, tenant_ids):
        return chunks

    def insert_citations(self, answer, content_ltks, vectors, embd_mdl, **_kwargs):
        # Return the answer unchanged; no citation markers inserted.
        return answer, set()


def _make_dialog():
    """Build a minimal dialog SimpleNamespace for async_chat()."""
    return SimpleNamespace(
        id="dialog-1",
        kb_ids=["kb-1"],
        tenant_id="tenant-1",
        tenant_llm_id=None,
        llm_id="gpt-4o",
        llm_setting={"temperature": 0.1},
        prompt_type="simple",
        prompt_config={
            "system": "You are helpful. {knowledge}",
            "parameters": [{"key": "knowledge", "optional": False}],
            "quote": True,
            "empty_response": "",
            "reasoning": False,
            "refine_multiturn": False,
            "cross_languages": False,
            "keyword": False,
            "toc_enhance": False,
            "tavily_api_key": "",
            "use_kg": False,
            "tts": False,
        },
        meta_data_filter={},
        similarity_threshold=0.2,
        vector_similarity_weight=0.3,
        top_n=6,
        top_k=1024,
        rerank_id="",
    )


def _stub_common(monkeypatch, chat_mdl, retriever, knowledges):
    monkeypatch.setattr(
        dialog_service, "get_model_type_by_name",
        lambda _tid, _llm_id: ["chat"]
    )
    monkeypatch.setattr(
        dialog_service, "get_model_config_from_provider_instance",
        lambda _tid, _type, _llm_id: _LLM_CONFIG,
    )
    monkeypatch.setattr(
        dialog_service.TenantLangfuseService, "filter_by_tenant",
        lambda tenant_id: None,
    )
    # get_models returns (kbs, embd_mdl, rerank_mdl, chat_mdl, tts_mdl)
    monkeypatch.setattr(
        dialog_service, "get_models",
        lambda _dialog: ([_KB], chat_mdl, None, chat_mdl, None),
    )
    monkeypatch.setattr(
        dialog_service.KnowledgebaseService, "get_field_map", lambda _kb_ids: {}
    )
    monkeypatch.setattr(
        dialog_service.KnowledgebaseService, "get_by_ids", lambda _ids: [_KB]
    )
    monkeypatch.setattr(dialog_service.settings, "retriever", retriever, raising=False)
    monkeypatch.setattr(dialog_service, "label_question", lambda _q, _kbs: "")
    monkeypatch.setattr(
        dialog_service, "kb_prompt",
        lambda _kbinfos, _max_tokens, **_kw: knowledges,
    )
    # Pin the input count so usage["input"] is deterministic.
    monkeypatch.setattr(
        dialog_service, "message_fit_in",
        lambda msg, _max_len: (_STUB_INPUT_TOKENS, msg),
    )


def _collect(async_gen):
    async def _run():
        return [ev async for ev in async_gen]
    return asyncio.run(_run())


def _assert_usage_shape(event):
    assert "usage" in event, f"Final answer must carry usage, got keys: {sorted(event.keys())}"
    usage = event["usage"]
    assert set(usage.keys()) == {"input", "output"}, f"usage must be {{input, output}}, got: {usage}"
    assert usage["input"] == _STUB_INPUT_TOKENS, (
        f"usage.input must be the message_fit_in count ({_STUB_INPUT_TOKENS}), got: {usage['input']}"
    )
    # decorate_answer() computes tk_num = num_tokens_from_string(think + answer)
    # and returns answer = think + answer, so output must match the local
    # num_tokens_from_string count of the returned answer text exactly.
    expected_output = dialog_service.num_tokens_from_string(event["answer"])
    assert usage["output"] == expected_output, (
        f"usage.output must match num_tokens_from_string(answer)={expected_output}, got: {usage['output']}"
    )
    assert isinstance(usage["input"], int) and isinstance(usage["output"], int)
    assert usage["output"] > 0


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.p2
def test_async_chat_stream_final_event_carries_usage(monkeypatch):
    """Streaming path: the final (final=True) event carries structured usage."""
    llm_answer = "RAGFlow handles document parsing with deep understanding."
    chat_mdl = _StreamingChatModel(llm_answer)
    _stub_common(monkeypatch, chat_mdl, _StubRetriever(), ["RAGFlow is a RAG engine."])

    dialog = _make_dialog()
    messages = [{"role": "user", "content": "What is RAGFlow?"}]

    events = _collect(dialog_service.async_chat(dialog, messages, stream=True, quote=True))

    final_events = [e for e in events if e.get("final") is True]
    assert len(final_events) == 1, f"Expected exactly one final event, got {len(final_events)}"
    _assert_usage_shape(final_events[0])

    # Purely additive: intermediate delta events must not carry usage.
    delta_events = [e for e in events if not e.get("final")]
    assert delta_events, "Expected at least one delta event"
    for ev in delta_events:
        assert "usage" not in ev, f"Delta event must not carry usage: {ev}"


@pytest.mark.p2
def test_async_chat_non_stream_result_carries_usage(monkeypatch):
    """Non-stream path: the single result event carries structured usage."""
    llm_answer = "RAGFlow is a RAG engine built for document understanding."
    chat_mdl = _StreamingChatModel(llm_answer)
    _stub_common(monkeypatch, chat_mdl, _StubRetriever(), ["RAGFlow is a RAG engine."])

    dialog = _make_dialog()
    messages = [{"role": "user", "content": "What is RAGFlow?"}]

    events = _collect(dialog_service.async_chat(dialog, messages, stream=False, quote=True))

    assert len(events) == 1, f"Non-stream async_chat must yield exactly one event, got {len(events)}"
    _assert_usage_shape(events[0])


@pytest.mark.p2
def test_async_chat_empty_response_path_has_no_usage(monkeypatch):
    """Documented boundary: the empty_response shortcut makes no LLM call and
    does not carry usage (downstream portal accounting falls back to
    estimation, same as for old RAGFlow versions)."""
    chat_mdl = _StreamingChatModel("unused")
    _stub_common(monkeypatch, chat_mdl, _StubRetriever(), [])

    dialog = _make_dialog()
    dialog.prompt_config["empty_response"] = "Sorry, no relevant content found."
    messages = [{"role": "user", "content": "What is RAGFlow?"}]

    events = _collect(dialog_service.async_chat(dialog, messages, stream=True, quote=True))

    final_events = [e for e in events if e.get("final") is True]
    assert len(final_events) == 1
    assert final_events[0]["answer"] == "Sorry, no relevant content found."
    assert "usage" not in final_events[0]


@pytest.mark.p2
def test_structure_answer_passes_usage_through():
    """structure_answer() (conversation_service) returns the same answer dict,
    so usage flows unchanged to the chatbot completions SSE frame / result."""
    ans = {
        "answer": "final answer text",
        "reference": {},
        "final": True,
        "usage": {"input": 11, "output": 22},
    }
    out = conversation_service.structure_answer(None, ans, "msg-1", "sess-1")
    assert out["usage"] == {"input": 11, "output": 22}
    assert out["id"] == "msg-1"
    assert out["session_id"] == "sess-1"
