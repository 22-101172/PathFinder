FROM python:3.11-slim

WORKDIR /app

COPY gateway/requirements.txt /tmp/gateway-requirements.txt
RUN pip install --no-cache-dir -r /tmp/gateway-requirements.txt

COPY gateway ./gateway
COPY engines ./engines
COPY data ./data

EXPOSE 8000
CMD ["uvicorn", "gateway.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
