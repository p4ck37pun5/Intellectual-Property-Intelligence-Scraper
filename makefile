.PHONY: install dev test test-integration lint typecheck docker-build docker-run clean help

install:  ## Install all dependencies (Node + Python)
	npm install
	pip install -r requirements.txt
	pip install pytest pytest-asyncio ruff mypy httpx
	@echo "✓ All dependencies installed"

install-model:  ## Pre-download embedding model (~80MB, speeds up first run)
	python3 -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2', cache_folder='/tmp/st_cache')"
	@echo "✓ Embedding model cached"


dev:  ## Run actor locally with storage/key_value_stores/default/INPUT.json
	npm run dev

run-python:  ## Run Python scraper directly (reads from stdin)
	@echo '{"queries":["blockchain"],"jurisdictions":["US"],"max_results":5}' \
	  | python3 src/main.py 2>&1 | python3 -c \
	  "import sys,json; d=json.load(sys.stdin); \
	   print(f'✓ {len(d)} patents'); \
	   [print(f'  {p[\"id\"]:20} | {p[\"title\"][:55]}') for p in d[:5]]"

run-with-embeddings:  ## Test embeddings pipeline (small batch)
	@echo '{"queries":["AI"],"jurisdictions":["US"],"max_results":3,"embeddings":true}' \
	  | python3 src/main.py 2>&1 | python3 -c \
	  "import sys,json; d=json.load(sys.stdin); \
	   print(f'✓ {len(d)} patents with embeddings'); \
	   [print(f'  {p[\"id\"]:20} | dims={len(p[\"embedding\"])}') for p in d[:3]]"

test:  ## Run unit tests (no network required)
	pytest tests/test_normalizer.py tests/test_utils.py -v

test-integration:  ## Run live API tests (requires internet)
	RUN_INTEGRATION=1 pytest tests/test_scrapers.py -v -s

test-all:  ## Run all tests including integration
	RUN_INTEGRATION=1 pytest tests/ -v -s

test-ci:  ## Run tests as in CI (unit only, coverage report)
	pytest tests/test_normalizer.py tests/test_utils.py -v \
	  --tb=short --no-header -q

lint:  ## Run ruff linter
	ruff check src/ tests/

lint-fix:  ## Auto-fix lint issues
	ruff check --fix src/ tests/

format:  ## Format code with ruff
	ruff format src/ tests/

typecheck:  ## Run mypy type checks
	mypy src/ --ignore-missing-imports --no-strict-optional

check: lint typecheck test  ## Run lint + typecheck + unit tests

docker-build:  ## Build Docker image
	docker build -t global-ip-scraper:latest .
	@echo "✓ Image built: global-ip-scraper:latest"

docker-run:  ## Run Docker image with local input
	docker run --rm \
	  -e APIFY_LOCAL_STORAGE_DIR=/usr/src/app/storage \
	  -v $(PWD)/storage:/usr/src/app/storage \
	  -v $(PWD)/.env:/usr/src/app/.env \
	  global-ip-scraper:latest
	@echo "✓ Check storage/datasets/default/ for output"

docker-size:  ## Show Docker image size breakdown
	docker images global-ip-scraper --format "table {{.Repository}}\t{{.Tag}}\t{{.Size}}"
	docker history global-ip-scraper:latest

push:  ## Push to Apify Store
	apify push

push-dry:  ## Dry-run push (validates without deploying)
	apify push --no-push

clean:  ## Remove caches, build artifacts, local scrape data
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null; true
	find . -name "*.pyc" -delete 2>/dev/null; true
	rm -rf .pytest_cache .mypy_cache .ruff_cache
	rm -rf storage/datasets/default/*.json
	rm -rf /tmp/ip_scraper_cache
	@echo "✓ Clean"

clean-model:  ## Remove cached embedding model
	rm -rf /tmp/st_cache
	@echo "✓ Model cache cleared"

help:  ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
	  | sort \
	  | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2}'

.DEFAULT_GOAL := help