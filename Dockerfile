# Use Python 3.11 slim as base image
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies for Manim
RUN apt-get update && apt-get install -y \
    libcairo2-dev \
    ffmpeg \
    texlive \
    texlive-latex-extra \
    texlive-fonts-extra \
    texlive-latex-recommended \
    texlive-science \
    tipa \
    libpango1.0-dev \
    pkg-config \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

# Copy dependency files
COPY pyproject.toml .
COPY requirements.txt .

# Install Python dependencies using uv
RUN uv pip install --system -r requirements.txt

# Copy application code
COPY . .

# Create necessary directories (per-job assets are created at runtime)
RUN mkdir -p media/jobs content public

# Expose the application port
EXPOSE 5000

# Set environment variables
ENV FLASK_APP=src/main.py
ENV PYTHONUNBUFFERED=1
# PORT must match EXPOSE / compose mapping; main.py honours $PORT (default 5000)
ENV PORT=5000

# Run the application
CMD ["python", "src/main.py"]
