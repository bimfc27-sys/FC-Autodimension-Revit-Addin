FROM python:3.12.8-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
RUN useradd --system --uid 10001 --home /app gateway && mkdir -p /data && chown gateway /data
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY camerascope_gateway.py tunnel_protocol.py ./
USER gateway
VOLUME ["/data"]
CMD ["python", "camerascope_gateway.py"]
