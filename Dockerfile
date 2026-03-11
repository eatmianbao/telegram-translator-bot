FROM python:3.12-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY bot.py .

# Railway sets PORT but we don't need it (polling mode).
# The bot runs as a long-lived process.
CMD ["python", "bot.py"]
