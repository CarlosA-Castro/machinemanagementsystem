# Use the slim image as a lightweight base
FROM python:3.13.5-slim

# Set the working directory
WORKDIR /app

# Copy your application code
COPY . /app

# Install dependencies (if any)
RUN pip install -r requirements.txt

EXPOSE 5000

# Command to run your application
CMD ["python3", "./maquinas-medellin-frontend/app.py"]
