# The free audit's browser: see server.py. Its own small image (no ML dependencies), run with a
# hard memory cap next to recsys-api.
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && playwright install --with-deps --only-shell chromium \
    && rm -rf /var/lib/apt/lists/* /root/.cache

COPY egress_proxy.py server.py ./
COPY tests ./tests

# Never root. Playwright starts Chromium without its own sandbox: the container is the boundary
# (memory cap, no capabilities, egress through egress_proxy.py only).
RUN useradd --create-home --uid 10001 renderer
USER renderer

EXPOSE 7000
CMD ["python", "server.py"]
