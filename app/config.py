from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-5-20250929"
    deepseek_api_key: str = ""
    deepseek_model: str = "deepseek-v4-flash"
    deepseek_thinking: bool = False
    google_api_key: str = ""
    gemini_model: str = "gemini-2.0-flash"
    openrouter_api_key: str = ""
    openrouter_model: str = "moonshotai/kimi-k2.6:free"
    openrouter_fallback_models: str = ""
    openrouter_max_retries: int = 3
    openrouter_retry_base_seconds: float = 2.0
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "gemma3:4b"
    whisper_model: str = "small"
    whisper_compute_type: str = "int8"
    youtube_cookie_file: str = ""
    summarizer: str = "ollama"  # "deepseek", "openrouter", "ollama", "claude", or "gemini"
    fallback_summarizer: str = ""  # fallback if primary fails ("" to disable)
    auth_password: str = ""  # set to enable password gate (leave empty to disable)
    cookie_secure: bool = False
    rate_limit: int = 10  # max requests per minute to expensive endpoints
    data_dir: str = "data"
    host: str = "0.0.0.0"
    port: int = 6999
    reload: bool = False

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
