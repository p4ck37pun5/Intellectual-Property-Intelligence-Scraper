# Intellectual Property Intelligence (IPI) Scraper — Apify Docker Image

FROM apify/actor-node-python:20-3.11

RUN apt-get update && apt-get install -y --no-install-recommends \
    libxml2-dev \
    libxslt-dev \
    libffi-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /usr/src/app

COPY package*.json ./
RUN npm install --omit=dev --prefer-offline \
    && echo "Node deps installed"

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && echo "Python deps installed"

# Pre-download embedding model to image (avoids cold-start latency)
# Remove this line if you want to save image size (80MB) and load at runtime
RUN python3 -c "\
from sentence_transformers import SentenceTransformer; \
SentenceTransformer('all-MiniLM-L6-v2', cache_folder='/tmp/st_cache'); \
print('Model cached')"

COPY . ./
CMD ["node", "src/main.js"]