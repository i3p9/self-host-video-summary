# Video Summarize

Self-hosted web app that transcribes YouTube audio locally with Whisper and summarizes it with local Ollama by default. Remote providers remain available as optional backends.

## Quick Start

```bash
# Copy config
cp .env.example .env

# Start the app + Ollama
docker compose up -d

# Pull the default local model (one-time)
docker compose exec ollama ollama pull gemma3:4b
```

App runs at `http://localhost:6999`

The default `.env.example` is already set up for local Ollama in Docker Compose. The important values are:

```bash
SUMMARIZER=ollama
OLLAMA_BASE_URL=http://ollama:11434
OLLAMA_MODEL=gemma3:4b
```

For a `4GB` VM, `WHISPER_MODEL=small` is the default target. If memory is tighter than expected, drop to `base` or `tiny`.

Completed summaries are persisted to SQLite, Whisper model downloads are cached, and transcripts are now cached by YouTube `video_id + whisper_model`. If a summary attempt fails after transcription, rerunning the same video can skip download/transcription and go straight to the LLM stage.

If the same video was already summarized with the same summarizer configuration, the app reuses the existing completed result instead of creating a duplicate job.

Each English summary is also translated to Bengali during the summarize stage and cached in SQLite with the job, so the Bengali view is ready when the result page loads.

OpenRouter free models can still be used, but they can hit rate limits. This app retries `429` and some `5xx` responses automatically, and it can fail over to additional OpenRouter models if you set `OPENROUTER_FALLBACK_MODELS`.

If YouTube starts returning `Sign in to confirm you're not a bot`, provide a `cookies.txt` file to `yt-dlp`. This app supports that through `YOUTUBE_COOKIE_FILE`.

## Deploy on Ubuntu / Hetzner

This repo is ready for a single-user or small shared deployment if you run one app instance, keep it behind a reverse proxy, and use password auth. It is not designed as a multi-worker or clustered service because active jobs live in memory while completed jobs are persisted to SQLite.

1. Install Docker, the Compose plugin, and Caddy on the VM.
2. Point a domain at the server.
3. Copy `.env.example` to `.env` and set at least:

```bash
SUMMARIZER=ollama
OLLAMA_BASE_URL=http://ollama:11434
OLLAMA_MODEL=gemma3:4b
WHISPER_MODEL=small
YOUTUBE_COOKIE_FILE=/app/secrets/youtube-cookies.txt
AUTH_PASSWORD=use-a-long-random-password
COOKIE_SECURE=true
BIND_HOST=127.0.0.1
HOST_PORT=6999
```

4. Start the app:

```bash
docker compose up -d --build
docker compose exec ollama ollama pull gemma3:4b
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

## YouTube Cookies

If `yt-dlp` gets blocked by YouTube, copy your exported `cookies.txt` file to the VM and mount it into the container through the built-in `./secrets` bind mount:

```bash
mkdir -p secrets
scp /path/to/cookies.txt root@your-server:~/self-host-video-summary/secrets/youtube-cookies.txt
chmod 600 secrets/youtube-cookies.txt
```

Then set this in `.env`:

```bash
YOUTUBE_COOKIE_FILE=/app/secrets/youtube-cookies.txt
```

And restart the app:

```bash
docker compose up -d --build
```

The repo ignores `secrets/`, and Docker excludes it from the build context, so the cookie file is mounted at runtime and not baked into the image.

## YouTube JS Challenges

Modern YouTube extraction also requires `yt-dlp`'s EJS companion scripts plus an external JavaScript runtime. This repo now installs both inside the app container:

- `yt-dlp[default]`, which includes `yt-dlp-ejs`
- `deno`, which `yt-dlp` recommends as the runtime for YouTube challenge solving

After pulling these changes, rebuild the app image:

```bash
docker compose up -d --build
docker compose exec app sh -lc 'deno --version && python -m yt_dlp --version'
```

If you are debugging outside the app container on the VM host, host-level `yt-dlp` and `deno` are separate from the container runtime. The app only uses what is installed inside the container.

## Configuration

Copy `.env.example` to `.env` and adjust as needed.

| Variable | Default | Description |
|---|---|---|
| `SUMMARIZER` | `ollama` | `deepseek`, `openrouter`, `ollama`, `claude`, or `gemini` |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama base URL. Use `http://ollama:11434` inside Docker Compose |
| `OLLAMA_MODEL` | `gemma3:4b` | Any model available in Ollama |
| `DEEPSEEK_API_KEY` | | Required if using DeepSeek |
| `DEEPSEEK_MODEL` | `deepseek-v4-flash` | DeepSeek model ID |
| `DEEPSEEK_THINKING` | `false` | Set to `true` to enable DeepSeek thinking mode |
| `OPENROUTER_API_KEY` | | Required if using OpenRouter |
| `OPENROUTER_MODEL` | `moonshotai/kimi-k2.6:free` | Any OpenRouter model ID |
| `OPENROUTER_FALLBACK_MODELS` | _(empty)_ | Comma-separated OpenRouter model IDs to try if the primary model fails |
| `OPENROUTER_MAX_RETRIES` | `3` | Automatic retries for `429` and transient `5xx` responses |
| `OPENROUTER_RETRY_BASE_SECONDS` | `2` | Base delay for exponential backoff when retrying OpenRouter |
| `WHISPER_MODEL` | `small` | Whisper model size (`tiny`, `base`, `small`, `medium`) |
| `YOUTUBE_COOKIE_FILE` | _(empty)_ | Path inside the container to a `cookies.txt` file for `yt-dlp` |
| `FALLBACK_SUMMARIZER` | _(empty)_ | Fallback provider if the primary summarizer fails (for example `openrouter`) |
| `AUTH_PASSWORD` | _(empty)_ | Password gate for the web UI; set this before exposing the app |
| `COOKIE_SECURE` | `false` | Set to `true` when serving the app over HTTPS |
| `ANTHROPIC_MODEL` | `claude-sonnet-4-5-20250929` | Claude model when `SUMMARIZER=claude` |
| `GEMINI_MODEL` | `gemini-2.0-flash` | Gemini model when `SUMMARIZER=gemini` |

Using Ollama keeps summarization local and avoids paid API usage. If you want the app to automatically try more than one OpenRouter model instead, keep your preferred model in `OPENROUTER_MODEL`, put alternates in `OPENROUTER_FALLBACK_MODELS`, and restart the app.

`BIND_HOST` and `HOST_PORT` control the Docker port mapping. The default is `127.0.0.1:6999`, which is the safer choice for reverse-proxy deployments.

## Optional Remote Providers

If you want to switch away from local Ollama:

```bash
SUMMARIZER=deepseek
DEEPSEEK_API_KEY=your-key-here
DEEPSEEK_MODEL=deepseek-v4-flash
```

Or:

```bash
SUMMARIZER=openrouter
OPENROUTER_API_KEY=your-key-here
OPENROUTER_MODEL=moonshotai/kimi-k2.6:free
```

Then restart the app. Claude and Gemini are also available through their respective API keys and model variables.

## Usage

1. Open `http://your-server:6999`
2. Paste a YouTube URL, click **Fetch Info**
3. Review video details, click **Summarize This Video**
4. Wait for download → transcription → summary
5. If the transcript for that video and Whisper model was already cached, the app skips directly to summary
6. If the exact same video was already summarized with the same summarizer config, the app reuses the existing result
7. The result page opens in `বাংলা` by default, with an `English` toggle available for the original summary.
8. View the full timestamped transcript

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

# Switch Ollama model
docker compose exec ollama ollama pull qwen3:4b
# Then update OLLAMA_MODEL in .env and restart app

# Add automatic OpenRouter model failover
# Set OPENROUTER_FALLBACK_MODELS=model-a,model-b in .env and restart app

# Switch to DeepSeek
# Set SUMMARIZER=deepseek and DEEPSEEK_API_KEY in .env, then restart app
```
