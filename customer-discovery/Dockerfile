FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY agent.py .

# State stored in /app (Railway handles persistence via volumes in dashboard)
ENV STATE_DIR=/app

CMD ["python", "agent.py", "--schedule"]
