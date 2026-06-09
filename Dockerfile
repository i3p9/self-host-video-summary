FROM python:3.12-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends curl ffmpeg unzip && \
    curl -fsSL https://deno.land/install.sh | sh -s -- --no-modify-path && \
    mv /root/.deno/bin/deno /usr/local/bin/deno && \
    chmod +x /usr/local/bin/deno && \
    rm -rf /root/.deno && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p data

EXPOSE 6999

CMD ["python", "run.py"]
