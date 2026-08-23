# SenseEdge: one-process-per-store edge runtime. CPU build of onnxruntime; swap in
# onnxruntime-gpu + an nvidia base image for CUDA (GPU is optional, P2).
FROM python:3.11-slim
ENV PYTHONIOENCODING=utf-8 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN apt-get update && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY packages/contracts /app/packages/contracts
COPY packages/sim /app/packages/sim
COPY packages/edgecv /app/packages/edgecv
COPY packages/edgeshelf /app/packages/edgeshelf
COPY packages/edgeanalytics /app/packages/edgeanalytics
COPY packages/edgequeue /app/packages/edgequeue
COPY packages/edgerules /app/packages/edgerules
COPY packages/edgestore /app/packages/edgestore
COPY packages/edgeuplink /app/packages/edgeuplink
COPY apps/senseedge /app/apps/senseedge
RUN pip install --no-cache-dir opencv-python-headless onnxruntime "uvicorn[standard]" paho-mqtt httpx \
 && for p in packages/contracts packages/sim packages/edgecv packages/edgeshelf packages/edgeanalytics \
             packages/edgequeue packages/edgerules packages/edgestore packages/edgeuplink apps/senseedge; do \
      pip install --no-cache-dir -e "$p"; done \
 && mkdir -p /data /app/models
EXPOSE 8001
CMD ["sh", "-c", "python -m senseedge --config ${RS_CONFIG} --port ${RS_EDGE_PORT:-8001} --cloud ${RS_CLOUD_URL} --detector ${RS_DETECTOR:-auto} --clock ${RS_CLOCK_FACTOR:-10} --uplink ${RS_UPLINK:-http}"]
