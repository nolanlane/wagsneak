# wagsneak

A Flask-based webhook service that checks Walgreens inventory for a given SKU and store, then updates an AppSheet table row with quantity, status, and any error details.

## Features
- Receives AppSheet webhook with `appsheet_row_id`, `product_id_18digit`, and `store_id`.  
- Optional HMAC authentication via `WEBHOOK_SECRET`.  
- Calls Walgreens Inventory API (full-dump) and filters for a single product.  
- Updates AppSheet row (`Quantity`, `Status`, `Error`) using the AppSheet REST API.

## Requirements
- Python 3.9 or higher  
- pip  
- Flask, requests, gunicorn (installed via `requirements.txt`)  
- waitress (for Windows, install separately via pip)

## Configuration
Set these environment variables before running:

| Variable                        | Description                                                 | Required | Default             |
|---------------------------------|-------------------------------------------------------------|----------|---------------------|
| WALGREENS_API_KEY               | Walgreens API Key                                           | Yes      | —                   |
| WALGREENS_AFFILIATE_ID          | Walgreens Affiliate ID                                      | Yes      | —                   |
| APPSHEET_API_KEY                | AppSheet Application Access Key                             | Yes      | —                   |
| APPSHEET_APP_ID                 | AppSheet App ID (found in its URL or Info tab)              | Yes      | —                   |
| APPSHEET_PRODUCT_TABLE_NAME     | Exact name of the table in your AppSheet app                | Yes      | —                   |
| WEBHOOK_SECRET                  | Secret for incoming webhook auth (optional)                 | No       | (empty = disabled)  |
| APPSHEET_KEY_COLUMN_NAME        | AppSheet key-column name; auto-detected if not provided     | No       | `Row ID`            |
| PORT                            | Port for the web server                                     | No       | `5000`              |

## Quickstart (Linux/macOS)

1. Clone the repo:
   ```bash
   git clone <your-fork-url>
   cd wagsneak
   ```
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Export environment variables:
   ```bash
   export WALGREENS_API_KEY="…"
   export WALGREENS_AFFILIATE_ID="…"
   export APPSHEET_API_KEY="…"
   export APPSHEET_APP_ID="…"
   export APPSHEET_PRODUCT_TABLE_NAME="…"
   export WEBHOOK_SECRET="…"      # optional
   export APPSHEET_KEY_COLUMN_NAME="Row ID"  # optional
   ```
4. Start the server with Gunicorn (recommended):
   ```bash
   gunicorn app:app \
       --worker-class gthread \
       --workers 1 \
       --threads 4 \
       -b 0.0.0.0:${PORT:-5000} \
       --timeout 120
   ```

## Quickstart (Windows)

1. Install Python 3.9+ (ensure `python` and `pip` are on your PATH).  
2. Clone the repo:
   ```powershell
   git clone <your-fork-url>
   cd wagsneak
   ```
3. Install dependencies:
   ```powershell
   pip install -r requirements.txt
   pip install waitress
   ```
4. Set environment variables (PowerShell):
   ```powershell
   $env:WALGREENS_API_KEY="…"
   $env:WALGREENS_AFFILIATE_ID="…"
   $env:APPSHEET_API_KEY="…"
   $env:APPSHEET_APP_ID="…"
   $env:APPSHEET_PRODUCT_TABLE_NAME="…"
   $env:WEBHOOK_SECRET="…"      # optional
   $env:APPSHEET_KEY_COLUMN_NAME="Row ID"  # optional
   $env:PORT="5000"             # optional
   ```
5. Run with the built-in helper (automatically uses Waitress on Windows):
   ```powershell
   python app.py
   ```

## Docker

Build and run via Docker:
```bash
docker build -t wagsneak .
docker run -e WALGREENS_API_KEY="…" \
           -e WALGREENS_AFFILIATE_ID="…" \
           -e APPSHEET_API_KEY="…" \
           -e APPSHEET_APP_ID="…" \
           -e APPSHEET_PRODUCT_TABLE_NAME="…" \
           -p 5000:5000 \
           wagsneak
```

## Testing the Endpoint

Send a sample webhook:
```bash
curl -X POST http://localhost:5000/check_walgreens_inventory \
     -H "Content-Type: application/json" \
     -H "X-Custom-Secret: <your-webhook-secret>" \
     --data '{"appsheet_row_id":"123", "product_id_18digit":"000000000000000123", "store_id":"0123"}'
```

## License

See [LICENSE](./LICENSE).