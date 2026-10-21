FROM python:3.12-slim-bookworm AS dependencies

RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 libglx-mesa0 libgl1-mesa-dri libegl1 libosmesa6 libglfw3 \
    xvfb xauth \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir --no-compile -r requirements.txt && pip check

FROM dependencies AS runtime
COPY environment/ ./environment/
COPY policies/ ./policies/
COPY log2motion_visualization.py ./
COPY scripts/smoke_cli.py ./scripts/smoke_cli.py
COPY docker-entrypoint.sh ./
RUN chmod +x /app/docker-entrypoint.sh

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    MUJOCO_GL=glfw LIBGL_ALWAYS_SOFTWARE=1
ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["--help"]
