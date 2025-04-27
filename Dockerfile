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

# No CMD or ENTRYPOINT needed here, as the RunPod UI's "Container Start Command" overrides this.
# Including CMD ["true"] or similar is sometimes done to make the Dockerfile technically executable,
# but for this RunPod setup, it's not strictly necessary.