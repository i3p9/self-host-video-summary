# Video Summarize

Self-hosted web app that transcribes and summarizes YouTube videos using local Whisper + Ollama.

## Setup

```bash
# Clone and start
docker compose up -d

# Pull the summarization model (one-time, ~3GB)
docker compose exec ollama ollama pull gemma3:4b
```

App runs at `http://localhost:6999`

## Configuration

Copy `.env.example` to `.env` and adjust as needed:

| Variable | Default | Description |
|---|---|---|
| `SUMMARIZER` | `ollama` | `ollama`, `openrouter`, `claude`, or `gemini` |
| `OLLAMA_MODEL` | `gemma3:4b` | Any model available in Ollama |
| `WHISPER_MODEL` | `base` | Whisper model size (`tiny`, `base`, `small`, `medium`) |
| `FALLBACK_SUMMARIZER` | _(empty)_ | Fallback if primary fails (e.g. `ollama`) |
| `OPENROUTER_API_KEY` | | Required if using OpenRouter |
| `OPENROUTER_MODEL` | `anthropic/claude-sonnet-4-5` | Any OpenRouter model |

## Usage

1. Open `http://your-server:6999`
2. Paste a YouTube URL, click **Fetch Info**
3. Review video details, click **Summarize This Video**
4. Wait for download → transcription → summary
5. View the summary and full timestamped transcript

## Managing

```bash
# View logs
docker compose logs -f app

# Update app after code changes (only rebuilds app, not Ollama)
docker compose up -d --build app

# Restart
docker compose restart

# Stop
docker compose down

# Switch Ollama model
docker compose exec ollama ollama pull qwen3:4b
# Then update OLLAMA_MODEL in .env and restart app
```
