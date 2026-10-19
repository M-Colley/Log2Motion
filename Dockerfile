FROM python:3.12-slim-bookworm AS dependencies

RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 libglx-mesa0 libgl1-mesa-dri libegl1 libosmesa6 libglfw3 \
    xvfb xauth x11vnc novnc websockify matchbox-window-manager \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir --no-compile -r requirements.txt && pip check

FROM dependencies AS runtime
COPY environment/ ./environment/
COPY policies/ ./policies/
COPY integration/ ./integration/
COPY log2motion_visualization.py ./
COPY docker-entrypoint.sh ./
RUN chmod +x /app/docker-entrypoint.sh

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    MUJOCO_GL=glfw LIBGL_ALWAYS_SOFTWARE=1 \
    LOG2MOTION_HOST=0.0.0.0 LOG2MOTION_DATA_DIR=/app/runtime/figma
EXPOSE 8080 6080
CMD ["/app/docker-entrypoint.sh"]
