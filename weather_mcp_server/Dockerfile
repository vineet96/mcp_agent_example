# Cloud Run takes the PORT env var and Python apps are expected to bind
# 0.0.0.0:$PORT. server.py already does that.
FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY server.py .

# Cloud Run will set PORT; default to 8080 for local docker runs.
ENV PORT=8080
EXPOSE 8080
CMD ["python", "server.py"]
