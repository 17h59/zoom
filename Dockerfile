FROM mcr.microsoft.com/playwright/python:v1.49.0-noble
RUN apt-get update && apt-get install -y --no-install-recommends pulseaudio pulseaudio-utils ffmpeg xvfb \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml ./
COPY zoombots ./zoombots
RUN pip install --no-cache-dir .
ENV ZOOMBOTS_HOST=0.0.0.0 ZOOMBOTS_DATA=/data ZOOMBOTS_SOUNDS=/sounds
VOLUME ["/data", "/sounds"]
EXPOSE 8000
# Un seul conteneur héberge le serveur PulseAudio et tous les bots : pas de coût d'un conteneur par bot.
CMD ["sh", "-c", "pulseaudio --start --exit-idle-time=-1 --disallow-exit && exec python -m zoombots"]
