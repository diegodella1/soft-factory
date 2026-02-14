FROM python:3.11-slim-bookworm

WORKDIR /app

# Install curl for web research page fetching
RUN apt-get update && apt-get install -y --no-install-recommends curl && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot/ bot/
COPY run.py .
COPY ORCHESTRATOR.md .

# Dashboard port
EXPOSE 3099

CMD ["python", "run.py"]
