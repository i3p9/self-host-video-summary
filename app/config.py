from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    anthropic_api_key: str = ""
    google_api_key: str = ""
    openrouter_api_key: str = ""
    openrouter_model: str = "anthropic/claude-sonnet-4-5"
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "gemma3:4b"
    whisper_model: str = "base"
    whisper_compute_type: str = "int8"
    summarizer: str = "openrouter"  # "openrouter", "ollama", "claude", or "gemini"
    fallback_summarizer: str = "ollama"  # fallback if primary fails ("" to disable)
    auth_password: str = ""  # set to enable password gate (leave empty to disable)
    rate_limit: int = 10  # max requests per minute to expensive endpoints
    data_dir: str = "data"
    host: str = "0.0.0.0"
    port: int = 6999

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
