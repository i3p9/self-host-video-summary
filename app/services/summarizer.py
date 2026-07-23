import logging
import time
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

_TRANSLATION_SYSTEM_PROMPT = """You are an expert translator. Translate English markdown into natural Bengali.

Rules:
1. Preserve the markdown structure, headings, bullets, emphasis, and spacing.
2. Translate the prose into clear Bengali.
3. Keep names, product names, URLs, and timestamps accurate.
4. Do not add commentary or wrap the output in code fences.

Return only the Bengali markdown."""

_TRANSLATION_USER_PROMPT_TEMPLATE = """Source Language: {source_language}
Target Language: {target_language}

Markdown Summary:
{text}"""


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


def _parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(float(value), 0.0)
    except ValueError:
        return None


def _parse_model_list(value: str) -> list[str]:
    models: list[str] = []
    for raw_model in value.split(","):
        model = raw_model.strip()
        if model and model not in models:
            models.append(model)
    return models


def _extract_error_detail(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return ""

    if not isinstance(payload, dict):
        return ""

    error = payload.get("error")
    if isinstance(error, str):
        return error.strip()
    if not isinstance(error, dict):
        return ""

    parts: list[str] = []
    message = error.get("message")
    if isinstance(message, str) and message.strip():
        parts.append(message.strip())

    code = error.get("code")
    if isinstance(code, str) and code.strip():
        parts.append(f"code={code.strip()}")

    return " | ".join(parts)


class Summarizer(ABC):
    provider: str = ""
    model: str = ""

    @abstractmethod
    def summarize(self, transcript: str, video_title: str) -> str:
        ...

    @abstractmethod
    def translate(
        self,
        text: str,
        source_language: str = "English",
        target_language: str = "Bengali",
    ) -> str:
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
        extra_body: dict | None = None,
        max_retries: int = 0,
        retry_base_seconds: float = 1.0,
    ):
        self.provider = provider
        self.model = model
        self.endpoint = endpoint.rstrip("/")
        self.timeout = timeout
        self.headers = dict(headers or {})
        self.extra_body = dict(extra_body or {})
        self.max_retries = max(max_retries, 0)
        self.retry_base_seconds = max(retry_base_seconds, 0.1)
        if api_key:
            self.headers["Authorization"] = f"Bearer {api_key}"

    def _build_summary_messages(self, transcript: str, video_title: str) -> list[dict[str, str]]:
        return [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {
                "role": "user",
                "content": _USER_PROMPT_TEMPLATE.format(
                    title=video_title, transcript=transcript
                ),
            },
        ]

    def _build_translation_messages(
        self,
        text: str,
        source_language: str,
        target_language: str,
    ) -> list[dict[str, str]]:
        return [
            {"role": "system", "content": _TRANSLATION_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": _TRANSLATION_USER_PROMPT_TEMPLATE.format(
                    source_language=source_language,
                    target_language=target_language,
                    text=text,
                ),
            },
        ]

    def _build_payload(
        self,
        messages: list[dict[str, str]],
        model: str,
        max_tokens: int = 4096,
    ) -> dict:
        payload = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": messages,
        }
        if self.extra_body:
            payload.update(self.extra_body)
        return payload

    def _retry_delay_seconds(self, response: httpx.Response, attempt: int) -> float:
        retry_after = _parse_retry_after(response.headers.get("retry-after"))
        if retry_after is not None:
            return retry_after
        return self.retry_base_seconds * (2 ** attempt)

    def _build_error(self, response: httpx.Response) -> Exception:
        detail = _extract_error_detail(response)
        if self.provider == "deepseek" and response.status_code == 429:
            message = (
                f"DeepSeek rate-limited model '{self.model}'. "
                "Wait and retry, or check your DeepSeek account balance and limits."
            )
            if detail:
                message = f"{message} ({detail})"
            return ValueError(message)
        if self.provider == "openrouter" and response.status_code == 429:
            message = (
                f"OpenRouter rate-limited model '{self.model}'. "
                "Wait and retry, switch to another OpenRouter model, or check your "
                "OpenRouter account limits."
            )
            if detail:
                message = f"{message} ({detail})"
            return ValueError(message)

        message = (
            f"{self.provider} returned HTTP {response.status_code} for model '{self.model}'"
        )
        if detail:
            message = f"{message}: {detail}"
        return httpx.HTTPStatusError(
            message,
            request=response.request,
            response=response,
        )

    def _generate_with_model(
        self,
        messages: list[dict[str, str]],
        model: str,
        max_tokens: int = 4096,
    ) -> str:
        payload = self._build_payload(messages, model, max_tokens=max_tokens)
        for attempt in range(self.max_retries + 1):
            resp = httpx.post(
                self.endpoint,
                headers=self.headers,
                json=payload,
                timeout=self.timeout,
            )
            if resp.is_success:
                self.model = model
                return _extract_chat_completion_text(resp.json())

            should_retry = resp.status_code in {429, 500, 502, 503, 504}
            if should_retry and attempt < self.max_retries:
                delay = self._retry_delay_seconds(resp, attempt)
                logger.warning(
                    "%s model '%s' returned HTTP %s; retrying in %.1fs (attempt %d/%d)",
                    self.provider,
                    model,
                    resp.status_code,
                    delay,
                    attempt + 1,
                    self.max_retries,
                )
                time.sleep(delay)
                continue

            self.model = model
            raise self._build_error(resp)

        raise RuntimeError("Unreachable summarizer retry state")

    def summarize(self, transcript: str, video_title: str) -> str:
        return self._generate_with_model(
            self._build_summary_messages(transcript, video_title),
            self.model,
        )

    def translate(
        self,
        text: str,
        source_language: str = "English",
        target_language: str = "Bengali",
    ) -> str:
        return self._generate_with_model(
            self._build_translation_messages(
                text,
                source_language=source_language,
                target_language=target_language,
            ),
            self.model,
        )


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

    def translate(
        self,
        text: str,
        source_language: str = "English",
        target_language: str = "Bengali",
    ) -> str:
        message = self.client.messages.create(
            model=self.model,
            max_tokens=4096,
            system=_TRANSLATION_SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": _TRANSLATION_USER_PROMPT_TEMPLATE.format(
                        source_language=source_language,
                        target_language=target_language,
                        text=text,
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

    def translate(
        self,
        text: str,
        source_language: str = "English",
        target_language: str = "Bengali",
    ) -> str:
        prompt = (
            _TRANSLATION_SYSTEM_PROMPT
            + "\n\n"
            + _TRANSLATION_USER_PROMPT_TEMPLATE.format(
                source_language=source_language,
                target_language=target_language,
                text=text,
            )
        )
        response = self.client.generate_content(prompt)
        return response.text


class DeepSeekSummarizer(ChatCompletionsSummarizer):
    def __init__(self):
        self.thinking_enabled = settings.deepseek_thinking
        super().__init__(
            provider="deepseek",
            model=settings.deepseek_model,
            endpoint="https://api.deepseek.com/chat/completions",
            timeout=120,
            api_key=_require_setting(
                settings.deepseek_api_key, "DEEPSEEK_API_KEY", "deepseek"
            ),
            extra_body={
                "thinking": {
                    "type": "enabled" if self.thinking_enabled else "disabled"
                }
            },
        )


class OpenRouterSummarizer(ChatCompletionsSummarizer):
    def __init__(self):
        models = _parse_model_list(
            ",".join(
                [
                    settings.openrouter_model,
                    settings.openrouter_fallback_models,
                ]
            )
        )
        if not models:
            raise ValueError("OPENROUTER_MODEL must not be empty when SUMMARIZER=openrouter")
        self.models = models
        super().__init__(
            provider="openrouter",
            model=models[0],
            endpoint="https://openrouter.ai/api/v1/chat/completions",
            timeout=120,
            api_key=_require_setting(
                settings.openrouter_api_key, "OPENROUTER_API_KEY", "openrouter"
            ),
            max_retries=settings.openrouter_max_retries,
            retry_base_seconds=settings.openrouter_retry_base_seconds,
        )

    def summarize(self, transcript: str, video_title: str) -> str:
        messages = self._build_summary_messages(transcript, video_title)
        errors: list[str] = []
        for idx, model in enumerate(self.models):
            try:
                return self._generate_with_model(messages, model)
            except Exception as exc:
                errors.append(f"{model}: {exc}")
                if idx == len(self.models) - 1:
                    break
                next_model = self.models[idx + 1]
                logger.warning(
                    "OpenRouter model '%s' failed: %s; trying fallback model '%s'",
                    model,
                    exc,
                    next_model,
                )

        raise ValueError("All configured OpenRouter models failed: " + "; ".join(errors))

    def translate(
        self,
        text: str,
        source_language: str = "English",
        target_language: str = "Bengali",
    ) -> str:
        messages = self._build_translation_messages(
            text,
            source_language=source_language,
            target_language=target_language,
        )
        errors: list[str] = []
        for idx, model in enumerate(self.models):
            try:
                return self._generate_with_model(messages, model)
            except Exception as exc:
                errors.append(f"{model}: {exc}")
                if idx == len(self.models) - 1:
                    break
                next_model = self.models[idx + 1]
                logger.warning(
                    "OpenRouter model '%s' failed translation: %s; trying fallback model '%s'",
                    model,
                    exc,
                    next_model,
                )

        raise ValueError("All configured OpenRouter models failed: " + "; ".join(errors))


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

    def translate(
        self,
        text: str,
        source_language: str = "English",
        target_language: str = "Bengali",
    ) -> str:
        try:
            result = self.primary.translate(
                text,
                source_language=source_language,
                target_language=target_language,
            )
            self.last_used = self.primary
            self.provider = self.primary.provider
            self.model = self.primary.model
            return result
        except Exception as e:
            logger.warning(
                "Primary summarizer (%s) failed translation: %s — falling back to %s",
                type(self.primary).__name__, e, type(self.fallback).__name__,
            )
            result = self.fallback.translate(
                text,
                source_language=source_language,
                target_language=target_language,
            )
            self.last_used = self.fallback
            self.provider = self.fallback.provider
            self.model = self.fallback.model
            return result


_SUMMARIZER_MAP = {
    "deepseek": DeepSeekSummarizer,
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


def configured_summarizer_model(name: str | None = None) -> str:
    summarizer_name = name or settings.summarizer
    models = {
        "deepseek": settings.deepseek_model,
        "openrouter": settings.openrouter_model,
        "ollama": settings.ollama_model,
        "claude": settings.anthropic_model,
        "gemini": settings.gemini_model,
    }
    return models.get(summarizer_name, "")


def format_summarizer_label(
    provider: str,
    model: str,
    thinking_enabled: bool | None = None,
) -> str:
    label = model or provider
    if provider == "deepseek":
        enabled = settings.deepseek_thinking if thinking_enabled is None else thinking_enabled
        mode = "thinking" if enabled else "non-thinking"
        return f"{label} ({mode})"
    return label


def configured_summarizer_label(name: str | None = None) -> str:
    summarizer_name = name or settings.summarizer
    thinking_enabled = settings.deepseek_thinking if summarizer_name == "deepseek" else None
    return format_summarizer_label(
        summarizer_name,
        configured_summarizer_model(summarizer_name),
        thinking_enabled=thinking_enabled,
    )


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
