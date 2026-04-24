FROM python:3.11-slim

# Install necessary system dependencies for RPi.GPIO and RPLCD
RUN apt-get update && apt-get install -y \
    gcc \
    iputils-ping \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirements and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application code
COPY power_monitor.py .
COPY config.ini .

# Run the python script
CMD ["python", "power_monitor.py"]
