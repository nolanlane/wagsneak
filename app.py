from flask import Flask, request, jsonify
import requests
import os
import datetime
import json # Import json for potential raw response handling and parsing errors
from dotenv import load_dotenv # Import load_dotenv

app = Flask(__name__)

# --- Configuration (Load from Environment Variables!) ---
# These variables MUST be set in your RunPod endpoint/server settings
# (for Serverless) or in the Pod's environment/systemd service
# configuration (for Ubuntu Pod).
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
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET") # Load optional secret

# Use the Production URL
APPSHEET_API_BASE_URL = f"https://api.appsheet.com/api/v2/apps/{APPSHEET_APP_ID}/tables/{APPSHEET_PRODUCT_TABLE_NAME}"


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
    # The body must be a list containing dictionaries for the rows to update.
    # Each dictionary MUST include the key column ('Row ID') and its value.
    appsheet_update_body = [
        {
            "Row ID": row_id,  # <-- Corrected: Use "Row ID" as the key name and the received row_id value
            "Quantity": quantity,
            "Status": status,
            "Error": error_message
            # Add other columns to update here if needed
        }
    ]

    try:
        print(f"Calling AppSheet API to update row {row_id}...")
        # print(f"AppSheet API Request Body: {json.dumps(appsheet_update_body)}") # Optional: Print request body for debugging

        appsheet_response = requests.post(
            appsheet_api_url,
            headers=headers,
            json=appsheet_update_body # Use json= for automatic encoding
        )

        print(f"AppSheet API Status Code: {appsheet_response.status_code}")

        # Check for successful response (2xx status codes)
        if appsheet_response.status_code >= 200 and appsheet_response.status_code < 300:
            print(f"Successfully updated AppSheet row {row_id}.")
            # You might want to parse the response body here if needed
            # print(f"AppSheet API Response Body: {appsheet_response.text}")
        else:
            print(f"Error calling AppSheet API for row {row_id}: {appsheet_response.status_code} {appsheet_response.reason} for url: {appsheet_api_url}")
            try:
                 # Attempt to print error details from response body
                 print(f"AppSheet API error response body: {appsheet_response.text}")
            except:
                 pass # ignore if response body not accessible

    except requests.exceptions.RequestException as e:
        print(f"Request error calling AppSheet API for row {row_id}: {e}")
    except Exception as e:
        # Catch any other unexpected errors during the AppSheet API update attempt
        print(f"Unexpected error during AppSheet API update attempt for row {row_id}: {e}")


# --- Flask Routes ---

@app.route('/check_walgreens_inventory', methods=['POST'])
def check_inventory():
    # --- Webhook Authentication (Optional) ---
    # If you set a WEBHOOK_SECRET environment variable, validate the header.
    if WEBHOOK_SECRET:
        # Adjust the header name if you configured a different one in AppSheet
        incoming_secret = request.headers.get("X-Custom-Secret")
        if incoming_secret != WEBHOOK_SECRET:
            print("Webhook secret validation failed.")
            return jsonify({"status": "error", "message": "Invalid secret"}), 401 # Unauthorized

    # --- Parse Incoming Webhook Data ---
    try:
        webhook_data = request.get_json()
        if not webhook_data:
            print("Received empty or invalid JSON body.")
            return jsonify({"status": "error", "message": "Invalid JSON"}), 400 # Bad Request

        # Extract data sent from AppSheet webhook
        # Ensure the keys here match EXACTLY what your AppSheet webhook JSON body is sending
        row_id = webhook_data.get("appsheet_row_id")
        product_id_18digit = webhook_data.get("product_id_18digit")
        store_id = webhook_data.get("store_id")
        app_version = webhook_data.get("app_version") # You might not need this, but good to capture

        if not row_id or not product_id_18digit or not store_id:
             print("Missing required data in webhook body.")
             return jsonify({"status": "error", "message": "Missing required data (appsheet_row_id, product_id_18digit, or store_id)"}), 400 # Bad Request

        print(f"Webhook received. Target Product ID: {product_id_18digit}, Store ID: {store_id}, AppSheet Row ID: {row_id}")

    except Exception as e:
        print(f"Error parsing incoming webhook data: {e}")
        return jsonify({"status": "error", "message": "Error processing webhook data"}), 400 # Bad Request


    # --- Call Walgreens API ---
    walgreens_api_url = "https://services.walgreens.com/api/products/inventory/v4"

    walgreens_payload = {
        "affId": WALGREENS_AFFILIATE_ID,
        "apiKey": WALGREENS_API_KEY,
        "availability": {
            "productIds": [product_id_18digit],
            "storeIds": [store_id]
        }
    }

    walgreens_headers = {
        "Content-Type": "application/json"
        # Add any other headers required by Walgreens API
    }

    try:
        print(f"Calling Walgreens API for store {store_id} at {walgreens_api_url}")
        walgreens_response = requests.post(walgreens_api_url, headers=walgreens_headers, json=walgreens_payload)
        print(f"Walgreens API Status Code: {walgreens_response.status_code}")

        # Check if Walgreens API call was successful
        if walgreens_response.status_code == 200:
            walgreens_data = walgreens_response.json()
            # --- Process Walgreens Response ---
            # Assuming the Walgreens response structure includes an 'inventoryStatus' or similar field
            inventory_status = "Unknown"
            quantity_available = None
            error_from_walgreens = None # Capture any specific errors from Walgreens response if possible

            found_item = False
            if walgreens_data and 'products' in walgreens_data and isinstance(walgreens_data['products'], list):
                 for product in walgreens_data['products']:
                      # Ensure comparison is type safe, Walgreens might return numbers or strings
                      if str(product.get('id')) == str(product_id_18digit):
                           print(f"Found item {product_id_18digit} in Walgreens response dump.")
                           found_item = True
                           # Assuming inventory status is in product['inventoryStatus'] and quantity in product['quantityAvailable']
                           inventory_status = product.get('inventoryStatus', 'Unknown')
                           quantity_available = product.get('quantityAvailable')
                           error_from_walgreens = product.get('message') # Or wherever Walgreens puts item-specific errors

                           # Map Walgreens status to your AppSheet status values
                           if inventory_status.lower() == 'instock':
                                inventory_for_appsheet = 'In Stock'
                           elif inventory_status.lower() == 'outofstock':
                                inventory_for_appsheet = 'Out of Stock'
                           else:
                                inventory_for_appsheet = 'Unknown' # Handle other statuses if any

                           # Format quantity for AppSheet
                           # Ensure quantity is a string, AppSheet often expects strings for text/number columns
                           quantity_for_appsheet = str(quantity_available) if quantity_available is not None else '0' # Default to '0' or empty string if no quantity found

                           # Update the AppSheet row with the found information
                           update_appsheet_row(
                                row_id, # Pass the row_id received from the webhook
                                quantity=quantity_for_appsheet,
                                status=inventory_for_appsheet,
                                error_message=error_from_walgreens # Pass any item-specific error
                           )
                           break # Exit loop once the target product is found

            if not found_item:
                 print(f"Target item {product_id_18digit} not found in Walgreens response data.")
                 # Update AppSheet indicating item not found or handle as an error
                 update_appsheet_row(
                      row_id, # Pass the row_id received from the webhook
                      quantity='0', # Or empty string depending on AppSheet column type
                      status='Not Found',
                      error_message=f"Item {product_id_18digit} not in Walgreens response."
                 )


        elif walgreens_response.status_code == 401:
             print("Walgreens API Authentication Failed (401). Check API Key/Affiliate ID.")
             update_appsheet_row(
                 row_id, # Pass the row_id received from the webhook
                 status='Error',
                 error_message='Walgreens API Auth Failed'
             )
        elif walgreens_response.status_code == 404:
             print("Walgreens API Resource Not Found (404). Check API URL or endpoints.")
             update_appsheet_row(
                 row_id, # Pass the row_id received from the webhook
                 status='Error',
                 error_message='Walgreens API URL Error'
             )
        else:
            # Handle other potential Walgreens API errors
            print(f"Walgreens API returned unexpected status code: {walgreens_response.status_code}")
            update_appsheet_row(
                row_id, # Pass the row_id received from the webhook
                status='Error',
                error_message=f'Walgreens API Error: {walgreens_response.status_code}'
            )


    except requests.exceptions.RequestException as e:
        print(f"Request error calling Walgreens API: {e}")
        # Update AppSheet with an error status due to request failure
        update_appsheet_row(
            row_id, # Pass the row_id received from the webhook
            status='Error',
            error_message=f'Walgreens API Request Failed: {e}'
        )
    except Exception as e:
        print(f"Unexpected error during Walgreens API call or processing: {e}")
        # Update AppSheet with a general error status
        update_appsheet_row(
            row_id, # Pass the row_id received from the webhook
            status='Error',
            error_message=f'Internal App Error: {e}'
        )


    # --- Respond to Webhook Sender (AppSheet) ---
    # AppSheet webhooks expect a 200 OK response to indicate success.
    # The actual data update happens via the AppSheet API call made above.
    return jsonify({"status": "success", "message": "Inventory check initiated and AppSheet update attempted."}), 200


# --- Local Development Server Entry Point ---
# When deploying on RunPod using Gunicorn or a similar server runner,
# this __name__ == '__main__': block is typically NOT executed.
# The server runner imports your 'app' instance directly.
# This block is ONLY for running the Flask development server locally for testing.
if __name__ == '__main__':
    print("--- Running Flask development server locally ---")

    # Check if required environment variables are set for local testing
    required_vars = ["WALGREENS_API_KEY", "WALGREENS_AFFILIATE_ID", "APPSHEET_API_KEY", "APPSHEET_APP_ID", "APPSHEET_PRODUCT_TABLE_NAME"]
    missing_vars = [var for var in required_vars if os.environ.get(var) is None]
    if missing_vars:
        print(f"Warning: Missing required environment variables for local testing: {', '.join(missing_vars)}")
        print("Please set these in your shell or a .env file.")


    # Use a different port than 8000 for local dev server to avoid conflicts
    # if testing against a running Pod.
    app.run(debug=True, host='0.0.0.0', port=5000)