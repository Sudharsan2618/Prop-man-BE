#!/bin/bash

echo "Testing Docker build locally..."

# Build the image
echo "Building Docker image..."
docker build -t luxelife-api .

# Run the container
echo "Running container on port 8080..."
docker run --rm -p 8080:8080 \
  -e DATABASE_URL="postgresql+asyncpg://test:test@localhost:5432/test" \
  -e REDIS_URL="redis://localhost:6379/0" \
  -e JWT_ACCESS_SECRET="test-access-secret-64-chars-long-here" \
  -e JWT_REFRESH_SECRET="test-refresh-secret-64-chars-long-here" \
  -e ALLOWED_ORIGINS="*" \
  -e STATIC_BASE_URL="http://localhost:8080" \
  -e GCS_BUCKET="test-bucket" \
  luxelife-api &
CONTAINER_ID=$!

# Wait for startup
echo "Waiting for container to start..."
sleep 10

# Test health endpoint
echo "Testing health endpoint..."
curl http://localhost:8080/health

# Stop container
echo "Stopping container..."
docker stop $CONTAINER_ID

echo "Test complete!"
