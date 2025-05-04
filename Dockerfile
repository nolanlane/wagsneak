# Use a suitable base image with Python pre-installed.
# python:3.9-slim-bookworm is recommended for a balance of size and features.
FROM python:3.9-slim-bookworm

# Set the working directory inside the container.
# This is where your repository contents will be cloned by RunPod by default.
WORKDIR /workspace

# Copy the requirements file into the container.
# Assuming RunPod places your cloned repo contents into WORKDIR (/workspace).
COPY requirements.txt .

# Install the Python dependencies.
# This happens during the image build process, making startup faster.
RUN pip install --no-cache-dir -r requirements.txt

# Copy your application code into the container.
# Assuming app.py is in the root of your repo.
COPY app.py .

# Expose the port your application will listen on (matches Flask's default port).
EXPOSE 5000

# Define default PORT and startup command for Cloud Run to launch the service.
ENV PORT 5000
# Increase Gunicorn worker timeout above the default 30s so long inventory pulls can complete
CMD exec gunicorn app:app \
    --worker-class gthread \
    --workers 1 \
    --threads 4 \
    --bind 0.0.0.0:$PORT \
    --timeout 120