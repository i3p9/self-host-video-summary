import logging
from abc import ABC, abstractmethod

import anthropic
import google.generativeai as genai
import httpx

from app.config import settings

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """You are an expert at summarizing video content. Given a video transcript and title, produce a clear, well-structured summary.

Your summary should include:
1. **Overview**: A 2-3 sentence high-level summary of the video.
2. **Key Points**: The main points or arguments made, as a bulleted list.
3. **Details & Examples**: Notable details, examples, or quotes mentioned.
4. **Takeaways**: Key conclusions or actionable takeaways.

Use markdown formatting. Be concise but comprehensive. Do not include preamble like "Here is a summary" — just output the summary directly."""

_USER_PROMPT_TEMPLATE = """Video Title: {title}

Transcript:
{transcript}"""


def _require_setting(value: str, env_var: str, provider: str) -> str:
    if value:
        return value
    raise ValueError(f"{env_var} is required when SUMMARIZER={provider}")


def _extract_chat_completion_text(payload: dict) -> str:
    content = payload["choices"][0]["message"]["content"]
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
                continue
            if not isinstance(item, dict):
                continue
            text = item.get("text")
            if isinstance(text, str) and text:
                parts.append(text)
        if parts:
            return "\n".join(parts).strip()
    raise ValueError("Chat completion response did not contain text content")


class Summarizer(ABC):
    provider: str = ""
    model: str = ""

    @abstractmethod
    def summarize(self, transcript: str, video_title: str) -> str:
        ...


class ChatCompletionsSummarizer(Summarizer):
    def __init__(
        self,
        *,
        provider: str,
        model: str,
        endpoint: str,
        timeout: float,
        api_key: str = "",
        headers: dict[str, str] | None = None,
    ):
        self.provider = provider
        self.model = model
        self.endpoint = endpoint.rstrip("/")
        self.timeout = timeout
        self.headers = dict(headers or {})
        if api_key:
            self.headers["Authorization"] = f"Bearer {api_key}"

    def summarize(self, transcript: str, video_title: str) -> str:
        resp = httpx.post(
            self.endpoint,
            headers=self.headers,
            json={
                "model": self.model,
                "max_tokens": 4096,
                "messages": [
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": _USER_PROMPT_TEMPLATE.format(
                            title=video_title, transcript=transcript
                        ),
                    },
                ],
            },
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return _extract_chat_completion_text(resp.json())


class ClaudeSummarizer(Summarizer):
    def __init__(self):
        self.provider = "claude"
        self.model = settings.anthropic_model
        self.client = anthropic.Anthropic(
            api_key=_require_setting(
                settings.anthropic_api_key, "ANTHROPIC_API_KEY", self.provider
            )
        )

    def summarize(self, transcript: str, video_title: str) -> str:
        message = self.client.messages.create(
            model=self.model,
            max_tokens=4096,
            system=_SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": _USER_PROMPT_TEMPLATE.format(
                        title=video_title, transcript=transcript
                    ),
                }
            ],
        )
        return message.content[0].text


class GeminiSummarizer(Summarizer):
    def __init__(self):
        self.provider = "gemini"
        self.model = settings.gemini_model
        genai.configure(
            api_key=_require_setting(
                settings.google_api_key, "GOOGLE_API_KEY", self.provider
            )
        )
        self.client = genai.GenerativeModel(self.model)

    def summarize(self, transcript: str, video_title: str) -> str:
        prompt = (
            _SYSTEM_PROMPT
            + "\n\n"
            + _USER_PROMPT_TEMPLATE.format(title=video_title, transcript=transcript)
        )
        response = self.client.generate_content(prompt)
        return response.text


class OpenRouterSummarizer(ChatCompletionsSummarizer):
    def __init__(self):
        super().__init__(
            provider="openrouter",
            model=settings.openrouter_model,
            endpoint="https://openrouter.ai/api/v1/chat/completions",
            timeout=120,
            api_key=_require_setting(
                settings.openrouter_api_key, "OPENROUTER_API_KEY", "openrouter"
            ),
        )


class OllamaSummarizer(ChatCompletionsSummarizer):
    def __init__(self):
        super().__init__(
            provider="ollama",
            model=settings.ollama_model,
            endpoint=f"{settings.ollama_base_url.rstrip('/')}/v1/chat/completions",
            timeout=600,  # local models can be slow on CPU
        )


class FallbackSummarizer(Summarizer):
    """Tries the primary summarizer, falls back to secondary on failure."""

    def __init__(self, primary: Summarizer, fallback: Summarizer):
        self.primary = primary
        self.fallback = fallback
        self.last_used = primary
        self.provider = primary.provider
        self.model = primary.model

    def summarize(self, transcript: str, video_title: str) -> str:
        try:
            result = self.primary.summarize(transcript, video_title)
            self.last_used = self.primary
            self.provider = self.primary.provider
            self.model = self.primary.model
            return result
        except Exception as e:
            logger.warning(
                "Primary summarizer (%s) failed: %s — falling back to %s",
                type(self.primary).__name__, e, type(self.fallback).__name__,
            )
            result = self.fallback.summarize(transcript, video_title)
            self.last_used = self.fallback
            self.provider = self.fallback.provider
            self.model = self.fallback.model
            return result


_SUMMARIZER_MAP = {
    "openrouter": OpenRouterSummarizer,
    "ollama": OllamaSummarizer,
    "claude": ClaudeSummarizer,
    "gemini": GeminiSummarizer,
}

_instance: Summarizer | None = None


def _build_summarizer(name: str) -> Summarizer:
    summarizer_cls = _SUMMARIZER_MAP.get(name)
    if summarizer_cls is None:
        raise ValueError(f"Unsupported summarizer '{name}'")
    return summarizer_cls()


def get_summarizer() -> Summarizer:
    global _instance
    if _instance is None:
        primary = _build_summarizer(settings.summarizer)

        fallback_name = settings.fallback_summarizer
        if fallback_name and fallback_name != settings.summarizer:
            _instance = FallbackSummarizer(primary, _build_summarizer(fallback_name))
        else:
            _instance = primary
    return _instance
