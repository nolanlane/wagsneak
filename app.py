from flask import Flask, request, jsonify
import requests
import os
import json # Import json for potential debugging output

app = Flask(__name__)

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

# --- Validate Required Environment Variables ---
required_vars = ["WALGREENS_API_KEY", "WALGREENS_AFFILIATE_ID", "APPSHEET_API_KEY", "APPSHEET_APP_ID", "APPSHEET_PRODUCT_TABLE_NAME"]
missing_vars = [var for var in required_vars if not os.environ.get(var)]
if missing_vars:
    # Log missing variables prominently - consider raising an error or exiting
    print(f"CRITICAL ERROR: Missing required environment variables: {', '.join(missing_vars)}")
    # Depending on deployment strategy, you might want to exit here
    # import sys
    # sys.exit(1)

# Define AppSheet base URL (without /Action or /Rows)
APPSHEET_API_BASE_URL = f"https://api.appsheet.com/api/v2/apps/{APPSHEET_APP_ID}/tables/{APPSHEET_PRODUCT_TABLE_NAME}" if APPSHEET_APP_ID and APPSHEET_PRODUCT_TABLE_NAME else None


# --- Helper Function to Update AppSheet ---
def update_appsheet_row(row_id, quantity=None, status=None, error_message=None):
    \"\"\"
    Updates a specific row in the AppSheet table via its API using the /Rows endpoint.

    Args:
        row_id (str): The unique key of the row to update (must match AppSheet key column name).
        quantity (str, optional): The quantity to update. Defaults to None.
        status (str, optional): The status to update. Defaults to None.
        error_message (str, optional): The error message to update. Defaults to None.
    \"\"\"
    # Ensure API base URL is configured before proceeding
    if not APPSHEET_API_BASE_URL or not APPSHEET_API_KEY:
        print("Error: AppSheet API URL or Key not configured due to missing environment variables.")
        return # Cannot proceed without config

    print(f"Attempting to update AppSheet row: {row_id} with Quantity='{quantity}', Status='{status}', Error='{error_message}'")

    appsheet_api_url = f"{APPSHEET_API_BASE_URL}/Rows" # Use the /Rows endpoint

    headers = {
        "Content-Type": "application/json",
        "ApplicationAccessKey": APPSHEET_API_KEY
    }

    # Construct the row data with the key column first
    # *** IMPORTANT: Verify "Row ID" is the EXACT key column name in AppSheet ***
    # If your key column is different (e.g., "ID"), change "Row ID" below.
    row_data_to_update = {
        "Row ID": row_id
    }

    # Add other columns to update, converting None to appropriate defaults
    if quantity is not None:
        # Ensure quantity is a string; default empty/None to '0'
        row_data_to_update["Quantity"] = str(quantity) if str(quantity) else '0'
    else:
        row_data_to_update["Quantity"] = '0' # Default if None

    if status is not None:
        row_data_to_update["Status"] = str(status)
    else:
        row_data_to_update["Status"] = '' # Default to empty string if None

    if error_message is not None:
        # Limit error message length if necessary for AppSheet column
        error_str = str(error_message)
        max_len = 250 # Example max length, adjust if needed
        row_data_to_update["Error"] = error_str[:max_len] if len(error_str) > max_len else error_str
    else:
        row_data_to_update["Error"] = '' # Default to empty string if None

    # Construct the final payload for the /Rows "Edit" action
    appsheet_payload = {
        "Action": "Edit",
        "Properties": {
            "Locale": "en-US", # Example: Specify locale if needed
            # "Timezone": "America/New_York", # Example: Specify timezone if needed
        },
        "Rows": [row_data_to_update] # Pass the row data in a list under "Rows"
    }

    try:
        print(f"Calling AppSheet API (Edit Row Action) at {appsheet_api_url}...")
        # print(f"AppSheet API Request Body: {json.dumps(appsheet_payload)}") # Uncomment to debug payload

        appsheet_response = requests.post( # POST method is correct for /Rows endpoint actions
            appsheet_api_url,
            headers=headers,
            json=appsheet_payload,
            timeout=30 # Add a timeout to prevent hanging indefinitely
        )

        print(f"AppSheet API Status Code: {appsheet_response.status_code}")

        if 200 <= appsheet_response.status_code < 300:
             # Consider checking response body for specific success indicators if API provides them
             print(f"Successfully initiated update for AppSheet row {row_id}.")
        else:
             print(f"Error calling AppSheet API for row {row_id}: {appsheet_response.status_code} {appsheet_response.reason}")
             try:
                   # Log the error response body for debugging
                   print(f"AppSheet API error response body: {appsheet_response.text}")
             except Exception as e:
                   print(f"Could not read AppSheet error response body: {e}")

    except requests.exceptions.Timeout:
        print(f"Request timed out calling AppSheet API for row {row_id}")
    except requests.exceptions.RequestException as e:
        print(f"Request error calling AppSheet API for row {row_id}: {e}")
    except Exception as e:
        print(f"Unexpected error during AppSheet API update attempt for row {row_id}: {e}")


@app.route('/check_walgreens_inventory', methods=['POST'])
def check_inventory():
    # --- Webhook Authentication (Optional) ---
    if WEBHOOK_SECRET:
        incoming_secret = request.headers.get("X-Custom-Secret")
        if incoming_secret != WEBHOOK_SECRET:
            print("Webhook secret validation failed.")
            return jsonify({"status": "error", "message": "Invalid secret"}), 401

    # --- Parse Incoming Webhook Data ---
    try:
        webhook_data = request.get_json()
        if not webhook_data:
            print("Received empty or invalid JSON body.")
            return jsonify({"status": "error", "message": "Invalid JSON"}), 400

        # Use .get with default=None to safely access keys
        row_id = webhook_data.get("appsheet_row_id")
        product_id_18digit = webhook_data.get("product_id_18digit")
        store_id = webhook_data.get("store_id")
        app_version = webhook_data.get("app_version", "1.0") # Provide a default app version if needed

        # Convert product_id to string immediately for consistent comparisons
        product_id_18digit_str = str(product_id_18digit) if product_id_18digit is not None else None

        print(f"DEBUG: product_id received: '{product_id_18digit}', type: {type(product_id_18digit)}. Using string: '{product_id_18digit_str}'")

        if not row_id or not product_id_18digit_str or not store_id:
             missing_params = [
                 p for p, v in
                 {
                     "appsheet_row_id": row_id,
                     "product_id_18digit": product_id_18digit_str,
                     "store_id": store_id
                 }.items() if not v
             ]
             print(f"Missing required data in webhook body: {', '.join(missing_params)}")
             return jsonify({"status": "error", "message": f"Missing required data: {', '.join(missing_params)}"}), 400

        print(f"Webhook received. Target Product ID: {product_id_18digit_str}, Store ID: {store_id}, AppSheet Row ID: {row_id}")

    except Exception as e:
        print(f"Error parsing incoming webhook data: {e}")
        return jsonify({"status": "error", "message": "Error processing webhook data"}), 400

    # --- Check if Walgreens API Credentials are Set ---
    if not WALGREENS_API_KEY or not WALGREENS_AFFILIATE_ID:
        print("Error: Walgreens API Key or Affiliate ID not configured.")
        # Update AppSheet with configuration error status
        update_appsheet_row(
            row_id,
            quantity='0',
            status='Error',
            error_message='Walgreens API credentials missing in server configuration.'
        )
        return jsonify({"status": "error", "message": "Walgreens API credentials not configured"}), 500

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
        print(f"Calling Walgreens API for store {store_id} at {walgreens_api_url}")
        walgreens_response = requests.post(
            walgreens_api_url,
            headers=walgreens_headers,
            json=walgreens_payload,
            timeout=30 # Add a timeout
        )
        print(f"Walgreens API Status Code: {walgreens_response.status_code}")

        if walgreens_response.status_code == 200:
            try:
                walgreens_data = walgreens_response.json()
                # --- Process Walgreens Response - Filter the dump ---
                quantity_for_appsheet = '0'
                inventory_for_appsheet = 'Not Found' # Default status if item not in dump
                error_for_appsheet = None
                found_item = False

                if isinstance(walgreens_data, list):
                     print(f"Received inventory dump with {len(walgreens_data)} items.")
                     for item in walgreens_data:
                          # Compare item ID from dump (as string) with target product ID (already string)
                          if str(item.get('id')) == product_id_18digit_str:
                               print(f"Found item {product_id_18digit_str} in Walgreens inventory dump.")
                               found_item = True
                               quantity_available = item.get('q') # Extract quantity ('q')

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
                                        print(f"Warning: Could not parse quantity '{quantity_available}' as integer for item {product_id_18digit_str}. Setting status to Unknown.")
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
                         print(f"Target item {product_id_18digit_str} not found in Walgreens inventory dump.")
                         update_appsheet_row(
                              row_id,
                              quantity='0',
                              status='Not Found',
                              error_message=f"Item {product_id_18digit_str} not in dump."
                         )

                # Handle cases where Walgreens API returns non-list data (e.g., error object)
                elif isinstance(walgreens_data, dict) and 'error' in walgreens_data:
                     error_detail = walgreens_data.get('error', 'Unknown Walgreens error')
                     print(f"Walgreens API returned an error object: {error_detail}")
                     update_appsheet_row(
                          row_id, quantity='0', status='Error',
                          error_message=f"Walgreens API Error: {error_detail}"
                     )
                else:
                     print("Walgreens API returned data in an unexpected format (not a list).")
                     update_appsheet_row(
                          row_id, quantity='0', status='Error',
                          error_message="Walgreens API returned unexpected data format."
                     )

            except json.JSONDecodeError:
                print("Error decoding JSON response from Walgreens API.")
                update_appsheet_row(
                    row_id, quantity='0', status='Error',
                    error_message='Failed to decode Walgreens API response.'
                )
            except Exception as e:
                print(f"Error processing Walgreens response: {e}")
                update_appsheet_row(
                    row_id, quantity='0', status='Error',
                    error_message=f'Error processing Walgreens data: {e}'
                )

        else:
            # Handle Walgreens API non-200 status codes
            print(f"Walgreens API returned non-200 status: {walgreens_response.status_code}")
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
        print(f"Request timed out calling Walgreens API.")
        update_appsheet_row(
            row_id, quantity='0', status='Error',
            error_message='Walgreens API request timed out.'
        )
    except requests.exceptions.RequestException as e:
        print(f"Request error calling Walgreens API: {e}")
        update_appsheet_row(
            row_id, quantity='0', status='Error',
            error_message=f'Walgreens API Request Failed: {e}'
        )
    except Exception as e:
        # Catch-all for any other unexpected errors during the process
        print(f"Unexpected error during inventory check: {e}")
        import traceback
        traceback.print_exc() # Print stack trace for detailed debugging
        update_appsheet_row(
            row_id, quantity='0', status='Error',
            error_message=f'Internal App Error: {e}'
        )

    # Always return a success response to the webhook sender if the request was processed
    # (even if errors occurred during API calls), unless it was a bad request initially.
    return jsonify({"status": "success", "message": "Inventory check processed; AppSheet update attempted."}), 200


if __name__ == '__main__':
    # Use Gunicorn or Waitress in production instead of app.run()
    # For RunPod, the Procfile or start command handles this.
    # This block might only be used for very basic local testing if needed.
    print("--- Starting Flask development server (for basic testing) ---")
    # Bind to 0.0.0.0 to be accessible externally (within container/network)
    # Port 5000 is common, adjust if needed.
    # Turn debug OFF for production/stable environments
    app.run(host='0.0.0.0', port=5000, debug=False)