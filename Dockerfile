# L'image Playwright embarque Chromium pour exactement cette version : la version pip doit être identique.
ARG PLAYWRIGHT_VERSION=1.49.0
FROM mcr.microsoft.com/playwright/python:v${PLAYWRIGHT_VERSION}-noble
ARG PLAYWRIGHT_VERSION
USER root
RUN apt-get update && apt-get install -y --no-install-recommends pulseaudio pulseaudio-utils ffmpeg xvfb \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /data /sounds /tmp/runtime && chown pwuser:pwuser /data /sounds /tmp/runtime && chmod 700 /tmp/runtime
WORKDIR /app
COPY pyproject.toml ./
COPY zoombots ./zoombots
RUN pip install --no-cache-dir "playwright==${PLAYWRIGHT_VERSION}" .
# Exécution sans root, PulseAudio en mode utilisateur. Le sandbox de Chromium demande des espaces de noms
# utilisateur ; dans un conteneur il est désactivé (ZOOMBOTS_SANDBOX=0) et l'isolation est celle du conteneur.
USER pwuser
ENV ZOOMBOTS_SANDBOX=0 ZOOMBOTS_HOST=0.0.0.0 ZOOMBOTS_DATA=/data ZOOMBOTS_SOUNDS=/sounds XDG_RUNTIME_DIR=/tmp/runtime
VOLUME ["/data", "/sounds"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/health' % os.environ.get('ZOOMBOTS_PORT', '8000'), timeout=3)"
# Un seul conteneur héberge le serveur PulseAudio et tous les bots : pas de coût d'un conteneur par bot.
CMD ["sh", "-c", "pulseaudio --start --exit-idle-time=-1 && exec python -m zoombots"]
