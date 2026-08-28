FROM python:3.12-slim
ENV DEBIAN_FRONTEND=noninteractive
ENV PATH="/root/.deno/bin:${PATH}"
ENV PORT=8000
ENV POT_PROVIDER_URL="http://127.0.0.1:4416"

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg curl unzip ca-certificates git \
    && rm -rf /var/lib/apt/lists/*

RUN curl -fsSL https://deno.land/install.sh | sh

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# BgUtils PO-token provider; keep provider and Python plugin on the same 1.3.x line.
RUN git clone --depth 1 --branch 1.3.1 \
    https://github.com/Brainicism/bgutil-ytdlp-pot-provider.git /opt/bgutil \
    && cd /opt/bgutil/server \
    && deno install --allow-scripts=npm:canvas --frozen

COPY main.py start.sh ./
RUN chmod +x start.sh
CMD ["./start.sh"]
