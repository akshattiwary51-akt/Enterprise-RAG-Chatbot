"""Chat model providers. The OpenAI adapter is the real one; FakeChatModel is a
deterministic, inspectable stand-in used by tests (it records every prompt it
receives so tests can assert exactly what reached the model)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Protocol


class LLMUnavailable(RuntimeError):
    """Model API misconfigured or unreachable (message is safe to show)."""


@dataclass
class LLMResult:
    text: str
    input_tokens: int | None = None
    output_tokens: int | None = None


class ChatModel(Protocol):
    model_name: str

    def generate(self, system: str, user: str) -> LLMResult: ...


class OpenAIChatModel:
    def __init__(self, model: str, api_key: str | None, base_url: str | None, timeout: int) -> None:
        self.model_name = model
        self._api_key, self._base_url, self._timeout = api_key, base_url, timeout
        self._client = None

    def _get(self):
        if not self._api_key:
            raise LLMUnavailable("Chat model is not configured: OPENAI_API_KEY is missing")
        if self._client is None:
            from langchain_openai import ChatOpenAI

            kwargs = {"model": self.model_name, "api_key": self._api_key, "temperature": 0,
                      "timeout": self._timeout, "max_retries": 2}
            if self._base_url:
                kwargs["base_url"] = self._base_url
            self._client = ChatOpenAI(**kwargs)
        return self._client

    def generate(self, system: str, user: str) -> LLMResult:
        from langchain_core.messages import HumanMessage, SystemMessage

        client = self._get()
        try:
            msg = client.invoke([SystemMessage(content=system), HumanMessage(content=user)])
        except Exception as exc:
            raise LLMUnavailable(f"Chat model error ({type(exc).__name__})") from exc
        usage = getattr(msg, "usage_metadata", None) or {}
        text = msg.content if isinstance(msg.content, str) else "".join(
            p.get("text", "") if isinstance(p, dict) else str(p) for p in msg.content
        )
        return LLMResult(text=text, input_tokens=usage.get("input_tokens"), output_tokens=usage.get("output_tokens"))


_PASSAGE_RE = re.compile(r'<passage id="(S\d+)"[^>]*>\n(.*?)\n</passage>', re.S)


@dataclass
class FakeChatModel:
    """Deterministic model for tests. Records every call.

    mode="extractive"  : answers with the first sentence of the top passage, citing it.
    mode="gullible"    : if any passage text contains an injected instruction, it obeys it by
                         echoing *all passages it was given* -- modelling a worst-case LLM so tests
                         can prove the attack surface is limited to authorized context.
    mode="fail"        : raises LLMUnavailable.
    mode="uncited"     : answers without any citation marker.
    mode="bad_cite"    : cites a passage id that was never supplied.
    """

    mode: str = "extractive"
    model_name: str = "fake-chat"
    calls: list[dict] = field(default_factory=list)

    def generate(self, system: str, user: str) -> LLMResult:
        self.calls.append({"system": system, "user": user})
        if self.mode == "fail":
            raise LLMUnavailable("Chat model error (FakeFailure)")
        passages = _PASSAGE_RE.findall(user)
        if not passages:
            return LLMResult("INSUFFICIENT_EVIDENCE", 10, 3)
        if self.mode == "uncited":
            return LLMResult(passages[0][1].split(".")[0].strip() + ".", 50, 10)
        if self.mode == "bad_cite":
            return LLMResult("Here is a fact [S99].", 50, 10)
        if self.mode == "gullible" and any(
            re.search(r"ignore (all|previous)|reveal", p[1], re.I) for p in passages
        ):
            dump = " ".join(f"{p[1]} [{p[0]}]" for p in passages)
            return LLMResult(dump, 100, 100)
        # Extractive: choose the sentence (across all passages) sharing the most words with the question.
        qm = re.search(r"Question: (.*)\Z", user, re.S)
        q_tokens = set(re.findall(r"[a-z0-9]+", (qm.group(1) if qm else "").lower()))
        best, best_score = None, -1
        for pid, text in passages:
            for sentence in re.split(r"(?<=[.!?])\s", text.strip()):
                score = len(q_tokens & set(re.findall(r"[a-z0-9]+", sentence.lower())))
                if score > best_score:
                    best, best_score = (pid, sentence), score
        pid, sentence = best
        return LLMResult(f"{sentence} [{pid}]", 80, 20)


def build_chat_model(cfg: dict) -> ChatModel:
    if cfg["LLM_PROVIDER"] == "fake":
        return FakeChatModel()
    return OpenAIChatModel(cfg["CHAT_MODEL"], cfg.get("OPENAI_API_KEY"), cfg.get("OPENAI_BASE_URL"),
                           cfg["MODEL_TIMEOUT_SECONDS"])
