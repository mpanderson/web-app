# RFA Matcher MVP

## Overview

The RFA Matcher MVP is a funding opportunity discovery and matching system designed to help researchers find relevant grants and research opportunities. The application scrapes funding opportunities from multiple sources (PCORI, RWJF, Gates Foundation, DoD SBIR, and others), stores them in a database, and uses semantic similarity matching to recommend opportunities based on researcher profiles.

The system provides a FastAPI-based backend that ingests funding data from various sources, generates embeddings for semantic search, and ranks opportunities using both vector similarity and optional LLM-based reranking.

## User Preferences

Preferred communication style: Simple, everyday language.

## System Architecture

### Backend Framework
**Problem:** Need a modern, fast Python web framework for building the API  
**Solution:** FastAPI with Uvicorn server  
**Rationale:** FastAPI provides automatic API documentation, type validation through Pydantic, async support, and excellent developer experience. It's well-suited for data-intensive applications requiring structured I/O validation.

### Database Layer
**Problem:** Need persistent storage for funding opportunities with flexible schema support  
**Solution:** SQLAlchemy ORM with SQLite (configurable to other databases via DATABASE_URL)  
**Rationale:** SQLAlchemy provides database-agnostic ORM capabilities, making it easy to switch between SQLite for development and PostgreSQL/MySQL for production. The schema stores opportunities with fields like title, summary, agency, eligibility, dates, and raw data as JSON for flexibility.

**Database Schema:**
- Single `opportunities` table with normalized fields (title, agency, mechanism, category, summary, eligibility, etc.)
- Stores both structured metadata and raw JSON for source-specific fields
- Content hash field for deduplication
- Date fields for posted_date and close_date to track opportunity windows

### Data Ingestion Architecture
**Problem:** Need to collect funding opportunities from diverse sources with different data formats  
**Solution:** Pluggable ingestor pattern with source-specific scrapers  
**Design Pattern:** Base class (`BaseIngestor`) with abstract `fetch()` and `normalize()` methods that each source implements

**Supported Sources:**
- PCORI (web scraping)
- Robert Wood Johnson Foundation (RWJF) (web scraping)
- Gates Foundation (web scraping)
- DoD SBIR (API + web scraping)
- NIH Guide (RSS + web scraping) - commented out
- Grants.gov (API) - commented out
- NSF (RSS) - commented out
- DARPA (RSS) - commented out

**Approach:** Each ingestor is registered in a central REGISTRY dictionary, making it easy to add new sources. The base class handles database session management and deduplication via content hashing.

### Semantic Search & Matching
**Problem:** Need to match researcher profiles to relevant funding opportunities beyond keyword matching  
**Solution:** Vector embeddings with cosine similarity search  

**Embedding Strategy:**
- Configurable backend (local SentenceTransformers or OpenAI embeddings)
- Local default: `sentence-transformers/all-MiniLM-L6-v2`
- OpenAI option: `text-embedding-3-small`
- Embeddings stored as numpy arrays on disk for fast retrieval

**Pros:** Semantic understanding captures meaning beyond keywords; configurable backends allow cost/quality tradeoffs  
**Cons:** Requires reindexing when opportunities change; local models have lower quality than commercial APIs

**Storage:** Embeddings stored in `/data` directory as:
- `opps_vecs.npy` - numpy array of vectors
- `opps_ids.json` - mapping to opportunity IDs

### LLM Reranking (Optional)
**Problem:** Vector similarity alone may miss nuanced fit criteria (eligibility, deadlines, mechanism constraints)  
**Solution:** Optional GPT-based reranking with explanations  
**Approach:** After vector search returns top-k candidates, GPT scores each 0-100 with reasoning. Falls back gracefully if API key missing.

**Pros:** Provides nuanced scoring and human-readable explanations  
**Cons:** Adds latency and cost; requires API quota management

### Scheduled Ingestion
**Problem:** Funding opportunities need to be refreshed regularly to stay current  
**Solution:** APScheduler background scheduler with twice-daily automated ingestion  
**Schedule:** Runs at 12:00 PM (noon) and 8:00 PM daily (America/New_York timezone)  
**Behavior:** 
- Automatically ingests from all registered sources (PCORI, Gates, RWJF, DoD SBIR)
- Reindexes vector embeddings after ingestion for semantic search
- Runs in background without blocking the API
- Can be monitored via `/scheduler/status` endpoint

**Implementation:** Uses APScheduler's BackgroundScheduler with cron triggers. Scheduler starts automatically when the FastAPI app starts and shuts down cleanly on app termination.

### Asynchronous Task Processing
**Problem:** Data ingestion and reindexing are long-running operations  
**Solution:** Celery task queue with Redis backend (optional, not actively used)  
**Configuration:** Tasks defined in `tasks.py` for ingesting individual sources or all sources, with automatic reindexing after completion

**Note:** Celery infrastructure (Redis, worker) must be set up separately - not embedded in the application. The APScheduler solution provides built-in scheduling without external dependencies.

### Web Scraping Strategy
**Problem:** Many funding sources don't provide APIs  
**Solution:** Respectful web scraping with rate limiting  
**Approach:**
- User-Agent identification for transparency
- Configurable delays between requests (typically 1-1.5 seconds)
- Exponential backoff for rate limit errors
- BeautifulSoup for HTML parsing
- Playwright/requests-html for JavaScript-rendered content

**Ethics:** Includes robots.txt checking (NIH scraper) and conservative crawling limits

### File Upload & Profile Extraction
**Problem:** Researchers may have profiles in various formats  
**Solution:** Multi-format profile parsing  
**Supported Formats:**
- Plain text input
- PDF extraction via pdfminer
- Text file reading

**Integration:** Profile text is cleaned and embedded using the same backend as opportunities for consistent semantic matching

## External Dependencies

### Core Framework
- **FastAPI** (0.115.4) - Web framework
- **Uvicorn** (0.30.6) - ASGI server with WebSocket support
- **Pydantic** (2.9.2) - Data validation and settings management
- **SQLAlchemy** (2.0.35) - Database ORM

### Data Processing
- **Pandas** (2.2.3) - Data manipulation and CSV handling
- **NumPy** (2.1.3) - Numerical operations and embedding storage
- **scikit-learn** (1.5.2) - Machine learning utilities (likely for similarity metrics)

### Web Scraping
- **Requests** (2.32.3) - HTTP client for API calls and scraping
- **BeautifulSoup4** (4.12.3) - HTML parsing
- **lxml** (5.3.0) - XML/HTML parser backend
- **lxml_html_clean** - HTML sanitization
- **Playwright** - Browser automation for JavaScript-heavy sites
- **requests-html** - Requests wrapper with JavaScript rendering

### Machine Learning & Embeddings
- **sentence-transformers** (3.1.1) - Local embedding models
- **OpenAI API** (optional) - Cloud-based embeddings and LLM reranking via `text-embedding-3-small` and `gpt-4o-mini`

### Scheduling & Task Queue
- **APScheduler** (3.11.0) - Background job scheduling for automated twice-daily ingestion
- **Celery** - Distributed task queue (configured but not actively used)
- **Redis** - Message broker and result backend (external dependency, configured via `settings.redis_url`)

### Additional Dependencies
- **feedparser** - RSS feed parsing for sources like NIH Guide, NSF, DARPA
- **dateparser** - Flexible date parsing across different formats
- **pdfminer** - PDF text extraction for profile uploads
- **python-multipart** - File upload handling

### Configuration
All external service connections are configured via environment variables or `.env` file:
- `DATABASE_URL` - Database connection (defaults to SQLite)
- `EMBEDDINGS_BACKEND` - "local" or "openai"
- `OPENAI_API_KEY` - For OpenAI embeddings/reranking
- `redis_url` - Redis connection for Celery (external service)
- `OFFLINE_DEMO` - Flag to use sample data instead of live sources