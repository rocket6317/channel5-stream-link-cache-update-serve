FROM python:3.13-slim

ARG CHANNEL5_BASE_ID=untracked
ARG CHANNEL5_SOURCE_DIGEST=untracked
LABEL io.channel5.base-image-id=$CHANNEL5_BASE_ID \
      io.channel5.source-digest=$CHANNEL5_SOURCE_DIGEST

RUN apt-get update \
    && apt-get upgrade -y --with-new-pkgs \
    && apt-get install -y --no-install-recommends chromium ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 1000 channel5 \
    && useradd --uid 1000 --gid 1000 --create-home channel5 \
    && mkdir /state && chown channel5:channel5 /state

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir --upgrade -r requirements.txt
COPY channel5.py renew.py server.py run.py ./

ENV CHANNEL5_CHROME=/usr/bin/chromium \
    CHANNEL5_STATE=/state/playback.json \
    CHANNEL5_BIND=0.0.0.0 \
    CHANNEL5_PORT=1996 \
    PYTHONUNBUFFERED=1
USER channel5
EXPOSE 1996
CMD ["python", "-u", "run.py"]
