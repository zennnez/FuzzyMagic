FROM python:3.10-slim

WORKDIR /app

# Install system dependencies if needed
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN pip install --no-cache-dir pandas openpyxl

# Copy the rest of the application
COPY . .

# Create uploads and downloads directories
RUN mkdir -p uploads downloads

# Expose the port
EXPOSE 8086

# Run the application
CMD ["python", "app.py"]
