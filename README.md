# 🌐 Intellectual Property Intelligence Scraper

> **Single query → USPTO + EPO + WIPO → normalized JSON → optional embeddings**

An [Apify Actor](https://apify.com/store) that solves fragmented patent data access for AI engineers, VCs, law firms, and R&D teams by providing unified, AI-ready patent JSON from three major jurisdictions in one run.

---

## ✨ Features

| Feature | Detail |
|---|---|
| **3 Jurisdictions** | USPTO (PatentsView), EPO (OPS), WIPO (PATENTSCOPE) |
| **Parallel Scraping** | All queries × all jurisdictions run concurrently via asyncio |
| **Unified Schema** | Every patent → same 10-field JSON regardless of source |
| **Semantic Embeddings** | Optional 384-dim `all-MiniLM-L6-v2` vectors, embed-ready |
| **Webhook** | POST notification on completion |
| **Retry Logic** | 3× retry with back-off per request |
| **Deduplication** | Cross-jurisdiction dedup by patent ID |
| **Memory Efficient** | Stream-pushes to Apify Dataset in batches |

---

## 🚀 Quick Start

### Option A — Run on Apify (no install)

1. Go to [apify.com/store](https://apify.com/store) and search **global-ip-intelligence**
2. Click **Try for free**
3. Enter your query and hit **Start**

### Option B — Local Development

```bash
# Prerequisites: Node 20+, Python 3.11+, pip

git clone https://github.com/your-org/global-ip-scraper
cd global-ip-scraper

npm install
pip install -r requirements.txt

# Create input.json
cat > storage/key_value_stores/default/INPUT.json << 'EOF'
{
  "queries": ["blockchain"],
  "jurisdictions": ["US"],
  "max_results": 20
}
EOF

# Run locally
npm run dev
```

### Option C — Docker

```bash
docker build -t global-ip-scraper .
docker run -e APIFY_TOKEN=your_token global-ip-scraper
```

---

## 📋 Input Parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `queries` | `string[]` | **required** | Keywords, company names, or IPC codes |
| `jurisdictions` | `enum[]` | `["US","EP"]` | `US`, `EP`, `WO` |
| `date_from` | `string` | `2020-01-01` | Earliest filing/publication date (YYYY-MM-DD) |
| `max_results` | `integer` | `100` | Total patents cap (1–5000) |
| `embeddings` | `boolean` | `false` | Generate 384-dim semantic vectors |
| `webhook_url` | `string` | `""` | POST completion summary here |

### Example Inputs

**Minimal:**
```json
{ "queries": ["quantum computing"] }
```

**Multi-jurisdiction with embeddings:**
```json
{
  "queries": ["CRISPR", "gene therapy"],
  "jurisdictions": ["US", "EP", "WO"],
  "date_from": "2022-01-01",
  "max_results": 500,
  "embeddings": true,
  "webhook_url": "https://your-server.com/hooks/patent-run"
}
```

---

## 📤 Output Schema

Every patent record in the Apify Dataset follows this exact structure:

```json
{
  "id": "US11256551B2",
  "title": "Blockchain-based virtual resource allocation",
  "abstract": "A system and method for allocating virtual resources...",
  "jurisdictions": ["US"],
  "filing_date": "2020-02-28",
  "publication_date": "2022-02-22",
  "assignee": "Alibaba Group Holding Limited",
  "inventors": ["Li Zhang", "Wei Chen"],
  "classifications": ["G06Q 50/18", "H04L 9/00"],
  "source_url": "https://patents.google.com/patent/US11256551B2",
  "embedding": [0.12, -0.45, 0.38, ...]
}
```

| Field | Type | Notes |
|---|---|---|
| `id` | `string` | Patent number (e.g. `US11256551B2`, `EP3945623A1`, `WO2022056789`) |
| `title` | `string` | Patent title |
| `abstract` | `string` | Full abstract text |
| `jurisdictions` | `string[]` | Source office codes |
| `filing_date` | `string` | ISO 8601 or `""` |
| `publication_date` | `string` | ISO 8601 or `""` |
| `assignee` | `string` | Primary assignee name |
| `inventors` | `string[]` | Inventor full names |
| `classifications` | `string[]` | CPC / IPC codes |
| `source_url` | `string` | Direct link to patent |
| `embedding` | `number[]` | 384-dim float array (empty when `embeddings: false`) |

---

## 🏗 Architecture

```
Input (JSON via Apify)
    │
    ▼
main.js (Node/Apify entrypoint)
    │ spawns
    ▼
main.py (asyncio orchestrator)
    │
    ├─── scrapers/uspto.py      ← PatentsView REST API
    ├─── scrapers/espacenet.py  ← EPO OPS REST API
    └─── scrapers/patentscope.py ← WIPO RSS / HTML
         │
         ▼
    normalizer.py (→ unified schema)
         │
         ▼
    embedder.py (optional: all-MiniLM-L6-v2)
         │
         ▼
    stdout (JSON array)
    │
    ▼
main.js pushData() → Apify Dataset → Webhook
```

---

## 🔑 Optional API Credentials

The scraper works without any API keys (public endpoints), but credentials unlock higher rate limits:

| Service | Env Var | How to get |
|---|---|---|
| EPO OPS | `EPO_KEY`, `EPO_SECRET` | [ops.epo.org](https://ops.epo.org) → Register |
| APIFY_TOKEN | auto-set by Apify | Your Apify account |

Set in Apify UI under **Actor → Environment Variables**, or locally in `.env`.

---

## 🧪 Test Cases

```bash
# Test 1: Basic USPTO query
echo '{"queries":["blockchain"],"jurisdictions":["US"],"max_results":50}' \
  | python3 src/main.py | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'{len(d)} patents')"

# Test 2: Embeddings
echo '{"queries":["AI"],"embeddings":true,"max_results":5}' \
  | python3 src/main.py | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(f'{len(d)} patents, embedding dims: {len(d[0][\"embedding\"])}')
"

# Test 3: Multi-jurisdiction
echo '{"queries":["quantum"],"jurisdictions":["US","EP","WO"],"max_results":30}' \
  | python3 src/main.py | python3 -c "
import sys, json
d = json.load(sys.stdin)
from collections import Counter
juris = Counter(p[\"jurisdictions\"][0] for p in d)
print(f'{len(d)} patents:', dict(juris))
"
```

---

## 📦 Deploy to Apify Store

```bash
# Install Apify CLI
npm install -g apify-cli

# Login
apify login

# Push (first time creates actor, subsequent pushes update it)
apify push

# Or run in cloud directly
apify call --input='{"queries":["robotics"],"max_results":100}'
```

---

## ⚙ Performance

| Scenario | Time | Memory |
|---|---|---|
| 100 patents, 1 jurisdiction | ~15s | ~120MB |
| 500 patents, 3 jurisdictions | ~60s | ~200MB |
| 1000 patents + embeddings | ~3min | ~450MB |
| 5000 patents + embeddings | ~12min | ~900MB |

> Recommend **1024MB** memory for runs with embeddings; **512MB** for plain scraping.

---

## 🛡 Error Handling

- **Retry**: Each HTTP request retried 3× with exponential back-off
- **Partial failure**: If one jurisdiction fails, others continue
- **Rate limiting**: Automatic delay between paginated requests
- **OOM guard**: Batch streaming to Apify Dataset (never loads all patents in RAM at once)
- **Webhook failure**: Logged as warning, does not fail the actor run

---

## 📄 License

ISC © Global IP Intelligence