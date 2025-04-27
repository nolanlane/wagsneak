from flask import Flask, request, jsonify
import requests
import os
import datetime
import json
from dotenv import load_dotenv

app = Flask(__name__)

# --- Configuration (Load from Environment Variables!) ---
# These variables MUST be set in your RunPod environment.
# DO NOT hardcode your API keys or sensitive info directly in this file!
#
# Required Environment Variables:
# WALGREENS_API_KEY: Your Walgreens API Key
# WALGREENS_AFFILIATE_ID: Your AffiliateID provided by Walgreens
# APPSHEET_API_KEY: Your AppSheet Application Access Key
# APPSHEET_APP_ID: Your AppSheet App ID (from its URL or Info tab)
# APPSHEET_PRODUCT_TABLE_NAME: The exact name of your table in AppSheet
# WEBHOOK_SECRET: (Optional) A secret string for webhook authentication

# Load variables from .env file ONLY FOR LOCAL TESTING
# On RunPod, these should be set in the environment directly.
load_dotenv()


WALGREENS_API_KEY = os.environ.get("WALGREENS_API_KEY")
WALGREENS_AFFILIATE_ID = os.environ.get("WALGREENS_AFFILIATE_ID")
APPSHEET_API_KEY = os.environ.get("APPSHEET_API_KEY")
APPSHEET_APP_ID = os.environ.get("APPSHEET_APP_ID")
APPSHEET_PRODUCT_TABLE_NAME = os.environ.get("APPSHEET_PRODUCT_TABLE_NAME")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET")

# Use the Production URL
APPSHEET_API_BASE_URL = f"[https://api.appsheet.com/api/v2/apps/](https://api.appsheet.com/api/v2/apps/){APPSHEET_APP_ID}/tables/{APPSHEET_PRODUCT_TABLE_NAME}"


# --- Helper Function to Update AppSheet ---
def update_appsheet_row(row_id, quantity=None, status=None, error_message=None):
    """
    Updates a specific row in the AppSheet table via its API.

    Args:
        row_id (str): The unique key ('Row ID') of the row to update.
        quantity (str, optional): The quantity to update. Defaults to None.
        status (str, optional): The status to update. Defaults to None.
        error_message (str, optional): The error message to update. Defaults to None.
    """
    print(f"Attempting to update AppSheet row: {row_id} with Quantity='{quantity}', Status='{status}', Error='{error_message}'")

    appsheet_api_url = f"{APPSHEET_API_BASE_URL}/Action"

    headers = {
        "Content-Type": "application/json",
        "ApplicationAccessKey": APPSHEET_API_KEY
    }

    # Construct the JSON body for the AppSheet API update call
    # Handle None values: Convert None to empty string or a default value for AppSheet
    # to avoid AppSheet API 'data is missing' errors.
    appsheet_update_data = {
        "Row ID": row_id # Use "Row ID" as the key name, required for update
    }

    # Explicitly add fields only if they are not None, or convert None to a suitable default.
    # This prevents sending 'null' if AppSheet doesn't like it for certain column types.

    # For 'Quantity', often expects a number or string representation of a number.
    if quantity is not None:
        appsheet_update_data["Quantity"] = str(quantity) if str(quantity) != '' else '0' # Ensure string, default to '0' if empty/None
    else:
        appsheet_update_data["Quantity"] = '0' # Send '0' if input quantity was explicitly None

    # For 'Status', typically expects a string.
    if status is not None:
        appsheet_update_data["Status"] = str(status)
    else:
        appsheet_update_data["Status"] = '' # Send empty string if status was explicitly None

    # For 'Error', typically expects a string.
    if error_message is not None:
        appsheet_update_data["Error"] = str(error_message)
    else:
        appsheet_update_data["Error"] = '' # Send empty string if error_message was explicitly None

    # Add other columns to update here with similar handling

    appsheet_update_body = [appsheet_update_data] # AppSheet API update expects a list of rows

    try:
        print(f"Calling AppSheet API to update row {row_id}...")
        # --- DEBUG PRINT URL for AppSheet API ---
        print(f"DEBUG URL: AppSheet API URL: '{appsheet_api_url}', type: {type(appsheet_api_url)}")
        # --- END DEBUG PRINT URL ---

        appsheet_response = requests.post(
            appsheet_api_url,
            headers=headers,
            json=appsheet_update_body # Use json= for automatic encoding
        )

        print(f"AppSheet API Status Code: {appsheet_response.status_code}")

        if appsheet_response.status_code >= 200 and appsheet_response.status_code < 300:
            print(f"Successfully updated AppSheet row {row_id}.")
        else:
            print(f"Error calling AppSheet API for row {row_id}: {appsheet_response.status_code} {appsheet_response.reason} for url: {appsheet_api_url}")
            try:
                 print(f"AppSheet API error response body: {appsheet_response.text}")
            except:
                 pass

    except requests.exceptions.RequestException as e:
        print(f"Request error calling AppSheet API for row {row_id}: {e}")
    except Exception as e:
        print(f"Unexpected error during AppSheet API update attempt for row {row_id}: {e}")

@app.route('/check_walgreens_inventory', methods=['POST'])
def check_inventory():
    # --- Webhook Authentication (Optional) ---
    # If you set a WEBHOOK_SECRET environment variable, validate the header.
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

        row_id = webhook_data.get("appsheet_row_id")
        product_id_18digit = webhook_data.get("product_id_18digit")
        store_id = webhook_data.get("store_id")
        app_version = webhook_data.get("app_version")

        # --- DEBUG PRINT 1: Immediately after reading webhook data ---
        print(f"DEBUG 1: product_id_18digit RECEIVED from webhook: '{product_id_18digit}', type: {type(product_id_18digit)}")
        # --- END DEBUG PRINT 1 ---


        if not row_id or not product_id_18digit or not store_id:
             print("Missing required data in webhook body.")
             return jsonify({"status": "error", "message": "Missing required data (appsheet_row_id, product_id_18digit, or store_id)"}), 400

        print(f"Webhook received. Target Product ID: {product_id_18digit}, Store ID: {store_id}, AppSheet Row ID: {row_id}")

    except Exception as e:
        print(f"Error parsing incoming webhook data: {e}")
        return jsonify({"status": "error", "message": "Error processing webhook data"}), 400

    # --- Call Walgreens API - Method B (Get full inventory dump, then filter) ---
    walgreens_api_url = "[https://services.walgreens.com/api/products/inventory/v4](https://services.walgreens.com/api/products/inventory/v4)" # Assuming same endpoint

    # Payload structure for Method B - does NOT include product ID in request body
    walgreens_payload = {
         "apiKey": WALGREENS_API_KEY,
         "affid": WALGREENS_AFFILIATE_ID,
         "store": store_id,
         "appVer": app_version # Include appVer as per snippet
         # Do NOT include product ID here
    }
    walgreens_headers = {"Content-Type": "application/json"}

    try:
        print(f"Calling Walgreens API for store {store_id} at {walgreens_api_url} (Method B)")
        # --- DEBUG PRINT URL for Walgreens API ---
        print(f"DEBUG URL: Walgreens API URL: '{walgreens_api_url}', type: {type(walgreens_api_url)}")
        # --- END DEBUG PRINT URL ---

        walgreens_response = requests.post(walgreens_api_url, headers=walgreens_headers, json=walgreens_payload)
        print(f"Walgreens API Status Code: {walgreens_response.status_code}")

        if walgreens_response.status_code == 200:
            walgreens_data = walgreens_response.json()
            # --- Process Walgreens Response - Filter the dump ---
            # Expected dump structure: list of dictionaries, each with keys 'id', 'q', 'ut', 'st'
            inventory_status = "Unknown"
            quantity_available = None
            error_from_walgreens = None

            found_item = False
            # Check if the response data is a list and contains inventory items
            if isinstance(walgreens_data, list):
                 print(f"Received inventory dump with {len(walgreens_data)} items.")

                 # --- DEBUG PRINT 2: Before iterating through dump ---
                 print(f"DEBUG 2: product_id_18digit before iterating dump: '{product_id_18digit}', type: {type(product_id_18digit)}")
                 # --- END DEBUG PRINT 2 ---

                 for item in walgreens_data:
                      # --- DEBUG PRINT 4: Inside the loop, showing item ID from dump ---
                      print(f"DEBUG 4: Checking item ID from dump: '{item.get('id')}', type: {type(item.get('id'))}")
                      # --- END DEBUG PRINT 4 ---

                      # Match item ID ('id') from the dump with the target product_id_18digit
                      # Ensure comparison is type safe (Walgreens might return numbers or strings for 'id')
                      # print(f"DEBUG: Comparing dump item '{item.get('id')}' to target '{product_id_18digit}'") # Optional detailed debug inside loop

                      if str(item.get('id')) == str(product_id_18digit): # <-- Uses product_id_18digit here
                           print(f"Found item {product_id_18digit} in Walgreens inventory dump.")
                           found_item = True
                           # Extract quantity ('q') and potentially status from the item data
                           quantity_available = item.get('q')
                           # You might need to derive status from quantity or another key if available in dump
                           # For simplicity, let's base status on quantity here
                           if quantity_available is not None and int(quantity_available) > 0:
                                inventory_for_appsheet = 'In Stock'
                           else:
                                inventory_for_appsheet = 'Out of Stock' # Assume Out of Stock if quantity is 0 or None

                           # Format quantity for AppSheet
                           quantity_for_appsheet = str(quantity_available) if quantity_available is not None else '0'

                           update_appsheet_row(
                                row_id,
                                quantity=quantity_for_appsheet,
                                status=inventory_for_appsheet,
                                # No specific item error available in dump structure described,
                                # so pass None or a default here if needed.
                                error_message=None
                           )
                           break # Exit loop once the target product is found

            # --- DEBUG PRINT 3: Inside the "not found" block ---
            if not found_item: # <-- This block is executed if item not found OR if product_id_18digit is truncated
                 print(f"DEBUG 3: product_id_18digit inside not found block: '{product_id_18digit}', type: {type(product_id_18digit)}")
                 # --- END DEBUG PRINT 3 ---

                 print(f"Target item {product_id_18digit} not found in Walgreens inventory dump.") # <-- Uses product_id_18digit here
                 update_appsheet_row(
                      row_id,
                      quantity='0',
                      status='Not Found',
                      error_message=f"Item {product_id_18digit} not in Walgreens dump." # <-- Uses product_id_18digit here
                 )

            # If walgreens_data is not a list, it might be an error response from Walgreens structured differently
            elif isinstance(walgreens_data, dict) and 'error' in walgreens_data:
                 print(f"Walgreens API returned an error in the response body: {walgreens_data.get('error')}")
                 update_appsheet_row(
                      row_id,
                      quantity='0',
                      status='Error',
                      error_message=f"Walgreens API Error in response: {walgreens_data.get('error')}"
                 )
            else:
                 print(f"Walgreens API returned data in unexpected format.")
                 update_appsheet_row(
                      row_id,
                      quantity='0',
                      status='Error',
                      error_message=f"Walgreens API returned unexpected data format."
                 )


        else:
            # Handle Walgreens API non-200 status codes (like 401, 404, 500)
            print(f"Walgreens API returned non-200 status code: {walgreens_response.status_code}")
            error_details = f'Walgreens API Error: {walgreens_response.status_code}'
            try:
                walgreens_error_body = walgreens_response.text
                if walgreens_error_body:
                     # Limit error message length for AppSheet column
                     error_details += f" - Details: {walgreens_error_body[:200]}"
            except:
                pass

            update_appsheet_row(
                row_id,
                quantity='0', # Send a default quantity like '0' in error case
                status='Error', # Set status to Error
                error_message=error_details # Pass combined error details
            )


    except requests.exceptions.RequestException as e:
        print(f"Request error calling Walgreens API: {e}")
        # Update AppSheet with an error status due to request failure
        update_appsheet_row(
            row_id,
            status='Error',
            error_message=f'Walgreens API Request Failed: {e}'
        )
    except Exception as e:
        print(f"Unexpected error during Walgreens API call or processing: {e}")
        # Update AppSheet with a general error status
        update_appsheet_row(
            row_id,
            status='Error',
            error_message=f'Internal App Error: {e}'
        )


    # --- Respond to Webhook Sender (AppSheet) ---
    # AppSheet webhooks expect a 200 OK response to indicate success.
    # The actual data update happens via the AppSheet API call made above.
    return jsonify({"status": "success", "message": "Inventory check initiated and AppSheet update attempted."}), 200


# --- Gunicorn/RunPod Entry Point ---
# When deploying on RunPod using Gunicorn or a similar server runner,
# this __name__ == '__main__': block is typically NOT executed.
# The server runner imports your 'app' instance directly.
# Keep this block empty for production deployment via Gunicorn/RunPod Endpoint.
if __name__ == '__main__':
    print("--- Running Flask development server locally ---")
    # For local testing, you might want to load environment variables from a .env file
    # if you have python-dotenv installed:
    try:
        from dotenv import load_dotenv
        load_dotenv()
        print(".env file loaded.")
    except ImportError:
        print("python-dotenv not installed. Please set environment variables manually.")

    # Check if required environment variables are set for local testing
    if not all([WALGREENS_API_KEY, WALGREENS_AFFILIATE_ID, APPSHEET_API_KEY, APPSHEET_APP_ID, APPSHEET_PRODUCT_TABLE_NAME]):
        print("Warning: Required environment variables not set. Local testing may fail.")

    app.run(debug=True, host='0.0.0.0', port=5000)