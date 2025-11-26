FROM python:3.11-slim

# Install system deps and Chrome
RUN apt-get update && apt-get install -y \
    wget gnupg unzip xvfb \
    && rm -rf /var/lib/apt/lists/*

# Install Chromium (or Google Chrome if you prefer that channel)
RUN apt-get update && apt-get install -y chromium chromium-driver && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONUNBUFFERED=1

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "10000"]