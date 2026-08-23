# SenseCloud + integrations + forecasting (the same image runs the Tally mock service).
FROM python:3.11-slim
ENV PYTHONIOENCODING=utf-8 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY packages/contracts /app/packages/contracts
COPY packages/forecasting /app/packages/forecasting
COPY packages/integrations /app/packages/integrations
COPY packages/sim /app/packages/sim
COPY apps/sensecloud /app/apps/sensecloud
RUN pip install --no-cache-dir "uvicorn[standard]" "psycopg[binary]" pandas scikit-learn httpx \
 && for p in packages/contracts packages/forecasting packages/integrations packages/sim apps/sensecloud; do \
      pip install --no-cache-dir -e "$p"; done \
 && mkdir -p /data
EXPOSE 8000
CMD ["python", "-m", "sensecloud"]
