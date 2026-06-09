# Video Summarize

Self-hosted web app that transcribes YouTube audio locally with Whisper and summarizes it with OpenRouter by default. Ollama remains available as an optional local backend.

## Quick Start

```bash
# Copy config and add your key
cp .env.example .env

# Start the app
docker compose up -d
```

App runs at `http://localhost:6999`

Set at least this in `.env` before starting:

```bash
OPENROUTER_API_KEY=your-key-here
SUMMARIZER=openrouter
OPENROUTER_MODEL=moonshotai/kimi-k2.6:free
```

For a `4GB` VM, `WHISPER_MODEL=small` is the default target. If memory is tighter than expected, drop to `base` or `tiny`.

## Deploy on Ubuntu / Hetzner

This repo is ready for a single-user or small shared deployment if you run one app instance, keep it behind a reverse proxy, and use password auth. It is not designed as a multi-worker or clustered service because active jobs live in memory while completed jobs are persisted to SQLite.

1. Install Docker, the Compose plugin, and Caddy on the VM.
2. Point a domain at the server.
3. Copy `.env.example` to `.env` and set at least:

```bash
OPENROUTER_API_KEY=your-key-here
OPENROUTER_MODEL=moonshotai/kimi-k2.6:free
WHISPER_MODEL=small
AUTH_PASSWORD=use-a-long-random-password
COOKIE_SECURE=true
BIND_HOST=127.0.0.1
HOST_PORT=6999
```

4. Start the app:

```bash
docker compose up -d --build
```

5. Put Caddy in front of it with a config like this:

```caddy
your.domain.example {
    encode zstd gzip
    reverse_proxy 127.0.0.1:6999
}
```

6. Open only `22`, `80`, and `443` in the Hetzner firewall and `ufw`. Do not expose `6999` publicly if you are using a reverse proxy.

7. Check the app at `https://your.domain.example`, log in, and run one test job to let Whisper download its model into the persistent cache volume.

If you do not want a public domain, the next best option is to keep `BIND_HOST=127.0.0.1` and access the VM through Tailscale or an SSH tunnel.

## Configuration

Copy `.env.example` to `.env` and adjust as needed.

| Variable | Default | Description |
|---|---|---|
| `SUMMARIZER` | `openrouter` | `openrouter`, `ollama`, `claude`, or `gemini` |
| `OPENROUTER_API_KEY` | | Required if using OpenRouter |
| `OPENROUTER_MODEL` | `moonshotai/kimi-k2.6:free` | Any OpenRouter model ID |
| `OLLAMA_MODEL` | `gemma3:4b` | Any model available in Ollama |
| `WHISPER_MODEL` | `small` | Whisper model size (`tiny`, `base`, `small`, `medium`) |
| `FALLBACK_SUMMARIZER` | _(empty)_ | Fallback if primary fails (e.g. `ollama`) |
| `AUTH_PASSWORD` | _(empty)_ | Password gate for the web UI; set this before exposing the app |
| `COOKIE_SECURE` | `false` | Set to `true` when serving the app over HTTPS |
| `ANTHROPIC_MODEL` | `claude-sonnet-4-5-20250929` | Claude model when `SUMMARIZER=claude` |
| `GEMINI_MODEL` | `gemini-2.0-flash` | Gemini model when `SUMMARIZER=gemini` |

Using OpenRouter means model switching is just a config change. If you want a different provider later, set `OPENROUTER_MODEL` to that model's OpenRouter ID and restart the app.

`BIND_HOST` and `HOST_PORT` control the Docker port mapping. The default is `127.0.0.1:6999`, which is the safer choice for reverse-proxy deployments.

## Optional Ollama

If you want to run local Ollama instead:

```bash
docker compose --profile local-llm up -d
docker compose --profile local-llm exec ollama ollama pull gemma3:4b
```

Then set this in `.env` and restart the app:

```bash
SUMMARIZER=ollama
OLLAMA_BASE_URL=http://ollama:11434
OLLAMA_MODEL=gemma3:4b
```

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

# Update app after code changes
docker compose up -d --build app

# Restart
docker compose restart

# Stop
docker compose down

# Switch OpenRouter model
# Edit OPENROUTER_MODEL in .env and restart app

# Switch Ollama model
docker compose --profile local-llm exec ollama ollama pull qwen3:4b
# Then update OLLAMA_MODEL in .env and restart app
```
