FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=3187 HOST=0.0.0.0 BACKEND_PORT=60372
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends nodejs fonts-dejavu-core && rm -rf /var/lib/apt/lists/*
COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu && pip install --no-cache-dir -r /app/backend/requirements.txt
COPY backend /app/backend
COPY public /app/public
COPY server.js /app/server.js
EXPOSE 3187
CMD ["sh", "-c", "cd /app/backend && python -m uvicorn app:app --host 127.0.0.1 --port 60372 & cd /app && exec node server.js"]
