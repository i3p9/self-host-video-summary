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


class Summarizer(ABC):
    @abstractmethod
    def summarize(self, transcript: str, video_title: str) -> str:
        ...


class ClaudeSummarizer(Summarizer):
    def __init__(self):
        self.client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    def summarize(self, transcript: str, video_title: str) -> str:
        message = self.client.messages.create(
            model="claude-sonnet-4-5-20250929",
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
        genai.configure(api_key=settings.google_api_key)
        self.model = genai.GenerativeModel("gemini-2.0-flash")

    def summarize(self, transcript: str, video_title: str) -> str:
        prompt = (
            _SYSTEM_PROMPT
            + "\n\n"
            + _USER_PROMPT_TEMPLATE.format(title=video_title, transcript=transcript)
        )
        response = self.model.generate_content(prompt)
        return response.text


class OpenRouterSummarizer(Summarizer):
    def __init__(self):
        self.api_key = settings.openrouter_api_key
        self.model = settings.openrouter_model

    def summarize(self, transcript: str, video_title: str) -> str:
        resp = httpx.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {self.api_key}",
            },
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
            timeout=120,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]


class OllamaSummarizer(Summarizer):
    def __init__(self):
        self.base_url = settings.ollama_base_url.rstrip("/")
        self.model = settings.ollama_model

    def summarize(self, transcript: str, video_title: str) -> str:
        resp = httpx.post(
            f"{self.base_url}/v1/chat/completions",
            json={
                "model": self.model,
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
            timeout=600,  # local models can be slow on CPU
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]


class FallbackSummarizer(Summarizer):
    """Tries the primary summarizer, falls back to secondary on failure."""

    def __init__(self, primary: Summarizer, fallback: Summarizer):
        self.primary = primary
        self.fallback = fallback

    def summarize(self, transcript: str, video_title: str) -> str:
        try:
            return self.primary.summarize(transcript, video_title)
        except Exception as e:
            logger.warning(
                "Primary summarizer (%s) failed: %s — falling back to %s",
                type(self.primary).__name__, e, type(self.fallback).__name__,
            )
            return self.fallback.summarize(transcript, video_title)


_SUMMARIZER_MAP = {
    "openrouter": OpenRouterSummarizer,
    "ollama": OllamaSummarizer,
    "claude": ClaudeSummarizer,
    "gemini": GeminiSummarizer,
}

_instance: Summarizer | None = None


def get_summarizer() -> Summarizer:
    global _instance
    if _instance is None:
        primary_cls = _SUMMARIZER_MAP.get(settings.summarizer, OpenRouterSummarizer)
        primary = primary_cls()

        fallback_name = settings.fallback_summarizer
        if fallback_name and fallback_name != settings.summarizer:
            fallback_cls = _SUMMARIZER_MAP.get(fallback_name)
            if fallback_cls:
                _instance = FallbackSummarizer(primary, fallback_cls())
            else:
                _instance = primary
        else:
            _instance = primary
    return _instance
