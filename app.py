from flask import Flask, request, jsonify
import requests
import os
import sys
import hmac
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor

app = Flask(__name__)
# Configure logging
logging.basicConfig(level=logging.INFO)
app.logger.setLevel(logging.INFO)

# --- Configuration (Load from Environment Variables) ---
# These variables MUST be set in your RunPod environment.
# Required Environment Variables:
# WALGREENS_API_KEY: Your Walgreens API Key
# WALGREENS_AFFILIATE_ID: Your AffiliateID provided by Walgreens
# APPSHEET_API_KEY: Your AppSheet Application Access Key
# APPSHEET_APP_ID: Your AppSheet App ID (from its URL or Info tab)
# APPSHEET_PRODUCT_TABLE_NAME: The exact name of your table in AppSheet
# WEBHOOK_SECRET: (Optional) A secret string for webhook authentication

WALGREENS_API_KEY = os.environ.get("WALGREENS_API_KEY")
WALGREENS_AFFILIATE_ID = os.environ.get("WALGREENS_AFFILIATE_ID")
APPSHEET_API_KEY = os.environ.get("APPSHEET_API_KEY")
APPSHEET_APP_ID = os.environ.get("APPSHEET_APP_ID")
APPSHEET_PRODUCT_TABLE_NAME = os.environ.get("APPSHEET_PRODUCT_TABLE_NAME")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET")
# Configurable AppSheet key-column name (default 'Row ID')
APPSHEET_KEY_COLUMN_NAME = os.environ.get("APPSHEET_KEY_COLUMN_NAME", "Row ID")

# --- Validate Required Environment Variables ---
required_vars = ["WALGREENS_API_KEY", "WALGREENS_AFFILIATE_ID", "APPSHEET_API_KEY", "APPSHEET_APP_ID", "APPSHEET_PRODUCT_TABLE_NAME"]
missing_vars = [var for var in required_vars if not os.environ.get(var)]
if missing_vars:
    app.logger.error(f"Missing required environment variables: {', '.join(missing_vars)}")
    sys.exit(1)

# Define AppSheet base URL (without /Action or /Rows)
APPSHEET_API_BASE_URL = (
    f"https://api.appsheet.com/api/v2/apps/{APPSHEET_APP_ID}/tables/{APPSHEET_PRODUCT_TABLE_NAME}"
    if APPSHEET_APP_ID and APPSHEET_PRODUCT_TABLE_NAME else None
)
# --- Auto-detect AppSheet key column via API if not explicitly configured ---
if APPSHEET_API_BASE_URL and APPSHEET_API_KEY and "APPSHEET_KEY_COLUMN_NAME" not in os.environ:
    try:
        cols_url = f"{APPSHEET_API_BASE_URL}/Columns"
        headers = {"Content-Type": "application/json", "ApplicationAccessKey": APPSHEET_API_KEY}
        payload = {"Action": "Get", "Properties": {}, "Rows": []}
        app.logger.info(f"Retrieving AppSheet columns metadata from {cols_url}")
        resp = requests.post(cols_url, headers=headers, json=payload, timeout=30)
        app.logger.info(f"AppSheet columns metadata HTTP status: {resp.status_code}")
        if resp.ok:
            data = resp.json()
            cols_list = data.get("Columns") if isinstance(data, dict) and "Columns" in data else data
            # Log available column names for debugging
            col_names = [c.get("Name") for c in cols_list if isinstance(c, dict)]
            app.logger.info(f"AppSheet columns: {col_names}")
            for col in cols_list:
                if col.get("Key") or col.get("IsKey"):
                    APPSHEET_KEY_COLUMN_NAME = col.get("Name")
                    app.logger.info(f"Detected AppSheet key column: {APPSHEET_KEY_COLUMN_NAME}")
                    break
        else:
            app.logger.warning(f"Could not fetch AppSheet columns metadata: HTTP {resp.status_code}")
    except Exception:
        app.logger.exception("Failed to auto-detect AppSheet key column")
else:
    app.logger.warning(
        f"Using AppSheet key column '{APPSHEET_KEY_COLUMN_NAME}'. "
        "Set APPSHEET_KEY_COLUMN_NAME env var to override."
    )


# --- Inventory cache (per-store) to minimize repeated Walgreens API calls ---
# --- Inventory cache (per-store) to minimize repeated Walgreens API calls ---
INVENTORY_CACHE = {}  # store_id -> {'timestamp': float, 'data': list}
CACHE_TTL = 10 * 60   # cache time-to-live in seconds (10 minutes)
 
# Executor for asynchronous AppSheet updates
_APPSHEET_EXECUTOR = ThreadPoolExecutor(max_workers=4)
 
# Helper to prune expired cache entries
def prune_cache():
    """
    Remove expired entries from INVENTORY_CACHE.
    """
    try:
        now = time.time()
        expired = [store for store, entry in INVENTORY_CACHE.items() if now - entry['timestamp'] >= CACHE_TTL]
        for store in expired:
            del INVENTORY_CACHE[store]
            app.logger.info(f"Pruned cached inventory for store {store}")
    except Exception as e:
        app.logger.warning(f"Error pruning cache: {e}")
#
# --- Helper Function to Update AppSheet ---
def update_appsheet_row(row_id, quantity=None, status=None, error_message=None):
    """
    Updates a specific row in the AppSheet table via its API using the /Rows endpoint.

    Args:
        row_id (str): The unique key of the row to update (must match AppSheet key column name).
        quantity (str, optional): The quantity to update. Defaults to None.
        status (str, optional): The status to update. Defaults to None.
        error_message (str, optional): The error message to update. Defaults to None.
    """
    # Ensure API base URL is configured before proceeding
    if not APPSHEET_API_BASE_URL or not APPSHEET_API_KEY:
        app.logger.error("AppSheet API URL or Key not configured. Skipping update.")
        return

    # Prepare REST call data
    appsheet_api_url = f"{APPSHEET_API_BASE_URL}/Rows"
    headers = {"Content-Type": "application/json", "ApplicationAccessKey": APPSHEET_API_KEY}
    # Build row payload
    row_data_to_update = {APPSHEET_KEY_COLUMN_NAME: row_id}
    row_data_to_update["Quantity"] = str(quantity) if quantity is not None else '0'
    row_data_to_update["Status"] = str(status) if status is not None else ''
    if error_message is not None:
        err = str(error_message)
        row_data_to_update["Error"] = err[:250] if len(err) > 250 else err
    else:
        row_data_to_update["Error"] = ''
    appsheet_payload = {"Action": "Edit", "Properties": {"Locale": "en-US"}, "Rows": [row_data_to_update]}

    def _send_update():
        try:
            app.logger.info(f"Updating AppSheet row {row_id}: Quantity={quantity!r}, Status={status!r}, Error={error_message!r}")
            app.logger.debug(f"Calling AppSheet API at {appsheet_api_url}")
            resp = requests.post(appsheet_api_url, headers=headers, json=appsheet_payload, timeout=30)
            app.logger.info(f"AppSheet API status: {resp.status_code}")
            if 200 <= resp.status_code < 300:
                app.logger.info(f"AppSheet update queued for row {row_id}")
            elif resp.status_code == 404:
                app.logger.error(f"AppSheet row not found (404): key '{APPSHEET_KEY_COLUMN_NAME}'='{row_id}'")
                app.logger.error(f"Response body: {resp.text}")
            else:
                app.logger.error(f"AppSheet error {resp.status_code}: {resp.reason}")
                app.logger.error(f"Body: {resp.text}")
        except requests.exceptions.Timeout:
            app.logger.error(f"Timeout when updating AppSheet row {row_id}")
        except requests.exceptions.RequestException as e:
            app.logger.error(f"RequestException updating AppSheet row {row_id}: {e}")
        except Exception:
            app.logger.exception(f"Unexpected error in AppSheet update for row {row_id}")

    _APPSHEET_EXECUTOR.submit(_send_update)


@app.route('/check_walgreens_inventory', methods=['POST'])
def check_inventory():
    # --- Webhook Authentication (Optional) ---
    if WEBHOOK_SECRET:
        incoming_secret = request.headers.get("X-Custom-Secret", "")
        if not hmac.compare_digest(incoming_secret, WEBHOOK_SECRET):
            app.logger.warning("Invalid webhook secret")
            return jsonify({"status": "error", "message": "Invalid secret"}), 401

    # --- Parse Incoming Webhook Data ---
    try:
        webhook_data = request.get_json()
        if not webhook_data:
            app.logger.warning("Received empty or invalid JSON body")
            return jsonify({"status": "error", "message": "Invalid JSON"}), 400

        # Extract and sanitize incoming values
        row_id_raw = webhook_data.get("appsheet_row_id")
        product_id_raw = webhook_data.get("product_id_18digit")
        store_id_raw = webhook_data.get("store_id")
        app_version = webhook_data.get("app_version", "1.0")

        # Normalize row_id preserving numeric types or trimming strings
        if row_id_raw is None:
            row_id = None
        elif isinstance(row_id_raw, str):
            row_id = row_id_raw.strip()
        else:
            row_id = row_id_raw
        # Convert product_id and store_id to trimmed strings
        product_id_18digit_str = str(product_id_raw).strip() if product_id_raw is not None else None
        store_id = str(store_id_raw).strip() if store_id_raw is not None else None

        app.logger.debug(
            f"product_id received raw: {product_id_raw!r} ({type(product_id_raw)}), using trimmed '{product_id_18digit_str}'"
        )

        if not row_id or not product_id_18digit_str or not store_id:
            missing_params = [
                p for p, v in
                {
                    "appsheet_row_id": row_id,
                    "product_id_18digit": product_id_18digit_str,
                    "store_id": store_id
                }.items() if not v
            ]
            app.logger.warning(f"Missing required data in webhook body: {', '.join(missing_params)}")
            return jsonify({"status": "error", "message": f"Missing required data: {', '.join(missing_params)}"}), 400

        app.logger.info(f"Webhook received: product_id={product_id_18digit_str}, store_id={store_id}, row_id={row_id}")

    except Exception as e:
        app.logger.exception(f"Error parsing incoming webhook data: {e}")
        return jsonify({"status": "error", "message": "Error processing webhook data"}), 400

    # --- Check if Walgreens API Credentials are Set ---
    if not WALGREENS_API_KEY or not WALGREENS_AFFILIATE_ID:
        app.logger.error("Walgreens API credentials not configured")
        # Update AppSheet with configuration error status
        update_appsheet_row(
            row_id,
            quantity='0',
            status='Error',
            error_message='Walgreens API credentials missing in server configuration.'
        )
        return jsonify({"status": "error", "message": "Walgreens API credentials not configured"}), 500

    # --- Cached inventory lookup (per-store) ---
    # Remove expired cache entries before lookup
    prune_cache()
    now = time.time()
    cache_entry = INVENTORY_CACHE.get(store_id)
    if cache_entry and now - cache_entry["timestamp"] < CACHE_TTL:
        app.logger.info(f"Using cached Walgreens inventory for store {store_id} (age {now - cache_entry['timestamp']:.0f}s)")
        # Use the pre-built inventory_map for fast lookups
        inventory_map = cache_entry.get("inventory_map", {})
        q = inventory_map.get(product_id_18digit_str)
        if q is not None:
            try:
                if int(q) > 0:
                    status_str = 'In Stock'
                    qty_str = str(q)
                else:
                    status_str = 'Out of Stock'
                    qty_str = '0'
            except (ValueError, TypeError):
                app.logger.warning(f"Could not parse quantity {q!r} for item {product_id_18digit_str}")
                status_str = 'Unknown Qty'
                qty_str = str(q)
            update_appsheet_row(row_id, quantity=qty_str, status=status_str, error_message=None)
        else:
            msg = f"Item {product_id_18digit_str} not in dump."
            app.logger.info(msg)
            update_appsheet_row(row_id, quantity='0', status='Not Found', error_message=msg)
        return jsonify({"status": "success", "message": "Inventory check processed; AppSheet update attempted."}), 200

    # --- Call Walgreens API - Method B (Get full inventory dump, then filter) ---
    walgreens_api_url = "https://services.walgreens.com/api/products/inventory/v4"

    walgreens_payload = {
         "apiKey": WALGREENS_API_KEY,
         "affid": WALGREENS_AFFILIATE_ID,
         "store": store_id,
         "appVer": app_version
    }
    walgreens_headers = {"Content-Type": "application/json"}

    try:
        app.logger.info(f"Calling Walgreens API for store {store_id} at {walgreens_api_url}")
        walgreens_response = requests.post(
            walgreens_api_url,
            headers=walgreens_headers,
            json=walgreens_payload,
            timeout=30 # Add a timeout
        )
        app.logger.info(f"Walgreens API status: {walgreens_response.status_code}")

        if walgreens_response.status_code == 200:
            try:
                walgreens_data = walgreens_response.json()
                # Build an index mapping product_id -> quantity for fast lookups
                inventory_map = {str(item.get('id')): item.get('q') for item in walgreens_data} if isinstance(walgreens_data, list) else {}
                # Cache this inventory map for 10 minutes
                try:
                    INVENTORY_CACHE[store_id] = {"timestamp": time.time(), "inventory_map": inventory_map}
                    app.logger.info(f"Cached Walgreens inventory for store {store_id} with {len(inventory_map)} items")
                except Exception as cache_err:
                    app.logger.warning(f"Failed to cache Walgreens inventory: {cache_err}")
                # Lookup specific product and update
                q = inventory_map.get(product_id_18digit_str)
                if q is not None:
                    try:
                        if int(q) > 0:
                            status_str = 'In Stock'
                            qty_str = str(q)
                        else:
                            status_str = 'Out of Stock'
                            qty_str = '0'
                    except (ValueError, TypeError):
                        app.logger.warning(f"Could not parse quantity {q!r} for item {product_id_18digit_str}")
                        status_str = 'Unknown Qty'
                        qty_str = str(q)
                    update_appsheet_row(row_id, quantity=qty_str, status=status_str, error_message=None)
                else:
                    msg = f"Item {product_id_18digit_str} not in dump."
                    app.logger.info(msg)
                    update_appsheet_row(row_id, quantity='0', status='Not Found', error_message=msg)
                return jsonify({"status": "success", "message": "Inventory check processed; AppSheet update attempted."}), 200
                # --- Legacy linear-scan block (retained for reference; not executed) ---
                """
                # Build an index mapping product_id -> quantity for fast lookups
                inventory_map = {}
                if isinstance(walgreens_data, list):
                    for item in walgreens_data:
                        key = str(item.get('id'))
                        inventory_map[key] = item.get('q')
                # Cache this inventory map for 10 minutes
                try:
                    INVENTORY_CACHE[store_id] = {"timestamp": time.time(), "inventory_map": inventory_map}
                    app.logger.info(f"Cached Walgreens inventory for store {store_id} with {len(inventory_map)} items")
                except Exception as cache_err:
                    app.logger.warning(f"Failed to cache Walgreens inventory: {cache_err}")
                # --- Process Walgreens Response using the index ---
                quantity_for_appsheet = '0'
                inventory_for_appsheet = 'Not Found'
                found_item = False
                if product_id_18digit_str in inventory_map:
                    found_item = True
                    q = inventory_map[product_id_18digit_str]
                    if q is not None:
                        try:
                            if int(q) > 0:
                                inventory_for_appsheet = 'In Stock'
                                quantity_for_appsheet = str(q)
                            else:
                                inventory_for_appsheet = 'Out of Stock'
                                quantity_for_appsheet = '0'
                        except (ValueError, TypeError):
                            app.logger.warning(f"Could not parse quantity {q!r} for item {product_id_18digit_str}")
                            inventory_for_appsheet = 'Unknown Qty'
                            quantity_for_appsheet = str(q)
                    else:
                        inventory_for_appsheet = 'Out of Stock'
                        quantity_for_appsheet = '0'

                               # Determine status and quantity string for AppSheet
                               if quantity_available is not None:
                                   try:
                                       if int(quantity_available) > 0:
                                           inventory_for_appsheet = 'In Stock'
                                           quantity_for_appsheet = str(quantity_available)
                                       else:
                                           inventory_for_appsheet = 'Out of Stock'
                                           quantity_for_appsheet = '0'
                                   except (ValueError, TypeError):
                                        app.logger.warning(f"Could not parse quantity {quantity_available!r} for item {product_id_18digit_str}; setting status to Unknown.")
                                        inventory_for_appsheet = 'Unknown Qty' # Or some other status
                                        quantity_for_appsheet = str(quantity_available) # Keep original string if not parseable int
                               else:
                                   inventory_for_appsheet = 'Out of Stock' # Treat null quantity as OOS
                                   quantity_for_appsheet = '0'

                               break # Exit loop once the target product is found

                     # Update AppSheet based on whether the item was found and its status
                     if found_item:
                         update_appsheet_row(
                             row_id,
                             quantity=quantity_for_appsheet,
                             status=inventory_for_appsheet,
                             error_message=None # Clear any previous error if found
                         )
                     else:
                         app.logger.info(f"Target item {product_id_18digit_str} not found in Walgreens inventory dump.")
                         update_appsheet_row(
                              row_id,
                              quantity='0',
                              status='Not Found',
                              error_message=f"Item {product_id_18digit_str} not in dump."
                         )

                """
                # Handle cases where Walgreens API returns an error object or unexpected format
                if isinstance(walgreens_data, dict) and 'error' in walgreens_data:
                     error_detail = walgreens_data.get('error', 'Unknown Walgreens error')
                     app.logger.error(f"Walgreens API returned an error object: {error_detail}")
                     update_appsheet_row(
                          row_id, quantity='0', status='Error',
                          error_message=f"Walgreens API Error: {error_detail}"
                     )
                else:
                     app.logger.error("Walgreens API returned unexpected data format (not a list)")
                     update_appsheet_row(
                          row_id, quantity='0', status='Error',
                          error_message="Walgreens API returned unexpected data format."
                     )

            except json.JSONDecodeError:
                app.logger.error("Error decoding JSON response from Walgreens API.")
                update_appsheet_row(
                    row_id, quantity='0', status='Error',
                    error_message='Failed to decode Walgreens API response.'
                )
            except Exception as e:
                app.logger.exception(f"Error processing Walgreens response: {e}")
                update_appsheet_row(
                    row_id, quantity='0', status='Error',
                    error_message=f'Error processing Walgreens data: {e}'
                )

        else:
            # Handle Walgreens API non-200 status codes
            app.logger.error(f"Walgreens API returned non-200 status: {walgreens_response.status_code}")
            error_details = f'Walgreens API Error: {walgreens_response.status_code}'
            try:
                # Try to get more details from the response body
                walgreens_error_body = walgreens_response.text
                if walgreens_error_body:
                     error_details += f" - Details: {walgreens_error_body[:200]}" # Limit length
            except Exception:
                pass # Ignore errors reading the error body

            update_appsheet_row(
                row_id, quantity='0', status='Error', error_message=error_details
            )

    except requests.exceptions.Timeout:
        app.logger.error("Request timed out calling Walgreens API.")
        update_appsheet_row(
            row_id, quantity='0', status='Error',
            error_message='Walgreens API request timed out.'
        )
    except requests.exceptions.RequestException as e:
        app.logger.error(f"Request error calling Walgreens API: {e}")
        update_appsheet_row(
            row_id, quantity='0', status='Error',
            error_message=f'Walgreens API Request Failed: {e}'
        )
    except Exception:
        # Catch-all for any other unexpected errors during the process
        app.logger.exception("Unexpected error during inventory check")
        update_appsheet_row(
            row_id, quantity='0', status='Error',
            error_message=f'Internal App Error: {e}'
        )

    # Always return a success response to the webhook sender if the request was processed
    # (even if errors occurred during API calls), unless it was a bad request initially.
    return jsonify({"status": "success", "message": "Inventory check processed; AppSheet update attempted."}), 200


if __name__ == '__main__':
    """
    Entry point for running the server.
    On Windows, use Waitress if available; on other OS, attempt to exec Gunicorn.
    Falls back to Flask's development server if the preferred WSGI server is not found.
    """
    import platform
    host = '0.0.0.0'
    port = int(os.environ.get('PORT', 5000))
    system = platform.system()
    if system == 'Windows':
        try:
            from waitress import serve
            app.logger.info(f"Detected Windows OS. Starting Waitress on {host}:{port}")
            serve(app, host=host, port=port)
        except ImportError:
            app.logger.warning("Waitress not installed. Falling back to Flask development server.")
            app.run(host=host, port=port, debug=False)
    else:
        # On Linux/Unix, try to launch via Gunicorn
        gunicorn_cmd = ["gunicorn", "app:app", "-b", f"{host}:{port}"]
        app.logger.info(f"Detected {system}. Starting Gunicorn: {' '.join(gunicorn_cmd)}")
        try:
            os.execvp("gunicorn", gunicorn_cmd)
        except OSError:
            app.logger.warning("Gunicorn not found. Falling back to Flask development server.")
            app.run(host=host, port=port, debug=False)