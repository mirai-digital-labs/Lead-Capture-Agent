# Lead Capture Agent (Web App + CLI)

A production-ready Google Maps & Website Email Lead Capture Agent built in Python with a modern **FastAPI Web Dashboard**, **Docker**, and **CLI**.

## Features
- **Web Dashboard (`app.py` + `web/index.html`):** Enter any search query, view captured leads live, and download formatted **Excel (`.xlsx`)** or **CSV (`.csv`)**.
- **Google Maps Scraper (`scraper/maps_scraper.py`):** Uses Playwright Chromium (free, zero API key required) or optional `SERPER_API_KEY`.
- **Website Email & Social Extractor (`scraper/email_extractor.py`):** Scans business homepages plus `/contact` and `/about` pages for emails and social links.
- **CLI Mode (`main.py`):** Also runnable directly from the terminal.

## Deploy Live from GitHub
1. Push this repository to **GitHub**.
2. Deploy the included `Dockerfile` (or `render.yaml`) on **Render**, **Railway**, **Fly.io**, or **Cloudflare Containers**.
3. (Optional) Put **Cloudflare** DNS/Proxy in front of your custom domain for SSL, caching, and DDoS protection.
