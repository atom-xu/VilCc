FROM python:3.11-slim

WORKDIR /app

# Install dependencies
RUN apt-get update && apt-get install -y \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY main.py fetcher.py ./
COPY .env.example ./.env

# Create empty cookies file
RUN touch cookies.txt

# Expose port
EXPOSE 8765

# Run the application
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8765"]
