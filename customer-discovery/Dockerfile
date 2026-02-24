FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY agent.py .

# Persistent state for dedup tracking
VOLUME /data
ENV STATE_DIR=/data

CMD ["python", "agent.py", "--schedule"]
