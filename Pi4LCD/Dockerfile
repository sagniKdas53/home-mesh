FROM python:3.11-slim

# Install necessary system dependencies for RPi.GPIO and RPLCD
RUN apt-get update && apt-get install -y \
    gcc \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirements and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application code
COPY lcd_display.py .

# Run the python script
CMD ["python", "lcd_display.py"]
