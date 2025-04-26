from flask import Flask, request, jsonify
import requests
import os
import datetime

app = Flask(__name__)

# --- Configuration (Load from Environment Variables!) ---
# You MUST set these environment variables in your RunPod endpoint/server settings.
# Do NOT hardcode your API keys or sensitive info here!
WALGREENS_API_KEY = os.environ.get("WALGREENS_API_KEY")
WALGREENS_AFFILIATE_ID = os.environ.get("WALGREENS_AFFILIATE_ID") # Your AffiliateID provided by Walgreens
APPSHEET_API_KEY = os.environ.get("APPSHEET_API_KEY") # Your AppSheet Application Access Key
APPSHEET_APP_ID = os.environ.get("APPSHEET_APP_ID") # Your AppSheet App ID (from its URL or Info tab)
APPSHEET_PRODUCT_TABLE_NAME = os.environ.get("APPSHEET_PRODUCT_TABLE_NAME") # The exact name of your table in AppSheet

# Use the Production URL based on the documentation
WALGREENS_API_URL = "https://services.walgreens.com/api/products/inventory/v4"

# --- Flask Endpoint to Receive AppSheet Webhook ---
# This route should match the URL path you configure in your AppSheet webhook automation.
@app.route('/check_walgreens_inventory', methods=['POST'])
def check_walgreens_inventory():
    # Optional: Implement webhook security check if you added a custom header in AppSheet
    # webhook_secret = os.environ.get("WEBHOOK_SECRET")
    # if webhook_secret and request.headers.get('X-Custom-Secret') != webhook_secret:
    #     print("Unauthorized webhook request (invalid secret header).")
    #     # Attempt to send error back to AppSheet if row_id is available
    #     data = request.json
    #     if data and data.get("appsheet_row_id"):
    #          update_appsheet_status(data.get("appsheet_row_id"), quantity="Error", status="Webhook Auth Fail", error_msg="Invalid webhook secret.")
    #     return jsonify({"status": "error", "message": "Unauthorized"}), 401


    data = request.json
    if not data:
        print("No JSON payload received from webhook.")
        # Cannot update AppSheet without row_id, just return error
        return jsonify({"status": "error", "message": "Invalid JSON payload"}), 400

    # Extract data sent from AppSheet webhook payload
    appsheet_row_id = data.get("appsheet_row_id")
    # This is the specific product ID we need to find *within* the Walgreens response dump
    product_id_18digit = data.get("product_id_18digit")
    store_id = data.get("store_id")
    app_version = data.get("app_version", "1.0") # Use provided version or default

    if not all([appsheet_row_id, product_id_18digit, store_id]):
        print(f"Missing required data in webhook: row_id={appsheet_row_id}, product_id={product_id_18digit}, store_id={store_id}")
        # Attempt to update AppSheet row with error status if row_id is present
        if appsheet_row_id:
             update_appsheet_status(appsheet_row_id, quantity="Error", status="Webhook Payload Fail", error_msg="Missing data in webhook payload.")
        return jsonify({"status": "error", "message": "Missing required data in payload"}), 400

    print(f"Received request for Product ID to find: {product_id_18digit}, Store ID: {store_id}, AppSheet Row ID: {appsheet_row_id}")

    # Initialize variables to store results
    walgreens_quantity = "N/A" # Default if not found or error
    walgreens_status = "Checking Inventory..." # Initial status
    error_message = None
    raw_response_snippet = "" # Store a snippet of the raw response for debugging

    try:
        # --- 1. Call Walgreens Inventory API (Requesting full store inventory) ---
        # Based on verification, the API doesn't filter by item ID in the request body.
        # It requires these 4 parameters to dump the store's inventory.
        walgreens_payload = {
            "apiKey": WALGREENS_API_KEY,
            "affid": WALGREENS_AFFILIATE_ID,
            "store": store_id,
            "appVer": app_version,
            # DO NOT include the specific product_id here based on your verification
        }

        walgreens_headers = {
            "Content-Type": "application/json",
            "Accept": "application/json"
        }

        print(f"Calling Walgreens API for store {store_id} at {WALGREENS_API_URL}")
        walgreens_response = requests.post( # Use POST method
            WALGREENS_API_URL,
            headers=walgreens_headers,
            json=walgreens_payload, # Send payload as JSON body
            timeout=60 # Increased timeout for potentially large responses
        )

        # Store a snippet of the response for logging/debugging, especially on errors
        raw_response_snippet = walgreens_response.text[:1000] + '...' if len(walgreens_response.text) > 1000 else walgreens_response.text
        print(f"Walgreens API Status Code: {walgreens_response.status_code}")
        # print(f"Walgreens API Response Snippet: {raw_response_snippet}") # Uncomment for detailed debugging

        # Check for HTTP errors (4xx or 5xx)
        walgreens_response.raise_for_status()

        # Parse the JSON response
        walgreens_data = walgreens_response.json()

        # --- 2. Parse the Full Walgreens Inventory Dump to Find the Specific Item ---
        # Based on the documentation snippet "id in the articleio,s is store, q is quantity, ut is updateTime"
        # and assuming a structure like {"inventoryList": [{"articleio": {id, q, ut}}, ...]} or similar.
        # You NEED to confirm the actual structure by inspecting a real response.

        inventory_list = []
        # Attempt to find the list containing item data
        if isinstance(walgreens_data, dict):
             # Common pattern: a key holding a list of items
             inventory_list = walgreens_data.get("inventoryList", []) # Assuming 'inventoryList' is the key

        # Ensure we are working with a list
        if not isinstance(inventory_list, list):
             print(f"Warning: Expected 'inventoryList' to be a list, got {type(inventory_list)}. Attempting to process as list.")
             inventory_list = [] # Treat as empty if not a list

        found_item_data = None
        if inventory_list:
             # Iterate through the list to find the item with the matching product_id_18digit
             for item_wrapper in inventory_list:
                 # Check if the item data is directly the dictionary, or nested in 'articleio'
                 item_data = item_wrapper.get("articleio") if isinstance(item_wrapper, dict) and "articleio" in item_wrapper else item_wrapper

                 if isinstance(item_data, dict) and item_data.get("id") == product_id_18digit:
                     found_item_data = item_data # Found the item's inventory data
                     print(f"Found item {product_id_18digit} in Walgreens response.")
                     break # Stop searching once found

        # --- 3. Extract Quantity and Determine Status ---
        if found_item_data:
            walgreens_quantity = found_item_data.get("q", "N/A") # Quantity is key "q"
            # Extract update time if needed (key "ut"), it's epoch time
            # update_time_epoch = found_item_data.get("ut")
            # if update_time_epoch:
            #      try:
            #          # Convert epoch timestamp to ISO format for AppSheet DateTime column
            #          last_checked_dt = datetime.datetime.fromtimestamp(int(update_time_epoch), datetime.timezone.utc)
            #      except:
            #           last_checked_dt = datetime.datetime.now(datetime.timezone.utc) # Fallback if epoch parsing fails
            # else:
            #      last_checked_dt = datetime.datetime.now(datetime.timezone.utc) # Fallback if ut key missing
            # For simplicity, we'll just use the current time for the AppSheet update timestamp

            # Determine status based on quantity or another key if available
            try:
                quantity_num = int(walgreens_quantity)
                if quantity_num > 0:
                    walgreens_status = "In Stock"
                else:
                     walgreens_status = "Out of Stock"
            except (ValueError, TypeError):
                 walgreens_status = "Quantity N/A or Unknown"

        else:
            # The item ID sent in webhook was not found in the inventory dump for that store
            walgreens_quantity = "N/A"
            walgreens_status = "Item Not Listed at Store"
            error_message = f"Product ID {product_id_18digit} not found in the inventory dump for store {store_id}."
            print(error_message)

    except requests.exceptions.Timeout:
        error_message = "Walgreens API request timed out."
        walgreens_status = "Error: Timeout"
        walgreens_quantity = "Error"
        print(error_message)
    except requests.exceptions.RequestException as e:
        error_message = f"Walgreens API request error: {e}. Status: {walgreens_response.status_code if 'walgreens_response' in locals() else 'N/A'}. Raw: {raw_response_snippet}"
        walgreens_status = f"Error: HTTP {walgreens_response.status_code if 'walgreens_response' in locals() else 'N/A'}"
        walgreens_quantity = "Error"
        print(error_message)
    except Exception as e:
        error_message = f"Error processing Walgreens response or unknown issue: {e}. Raw: {raw_response_snippet}"
        walgreens_status = "Error: Processing Failed"
        walgreens_quantity = "Error"
        print(error_message)

    finally:
        # --- 4. Update AppSheet via API ---
        # Call the helper function to update the specific row in AppSheet
        print(f"Attempting to update AppSheet row: {appsheet_row_id}")
        update_appsheet_status(
            row_id=appsheet_row_id,
            quantity=walgreens_quantity,
            status=walgreens_status,
            error_msg=error_message
        )

    # Return a success response to the AppSheet webhook.
    # AppSheet expects a 200 status code to consider the webhook successful.
    # The body content here is less important for AppSheet itself.
    return jsonify({"status": "processing_complete", "message": "Walgreens inventory dump retrieved, item searched, and AppSheet update attempted."}), 200


# --- Helper Function to Update AppSheet Row via API ---
def update_appsheet_status(row_id, quantity, status, error_msg=None):
    """Calls the AppSheet API to update a specific row."""
    # Ensure configuration is available
    if not all([APPSHEET_APP_ID, APPSHEET_PRODUCT_TABLE_NAME, APPSHEET_API_KEY, row_id]):
         print("Error: AppSheet update configuration missing (App ID, Table Name, API Key, or Row ID). Cannot update AppSheet row.")
         return # Cannot update AppSheet if essential config or row_id is missing

    appsheet_url = f"https://api.appsheet.com/api/v2/apps/{APPSHEET_APP_ID}/tables/{APPSHEET_PRODUCT_TABLE_NAME}/Action"
    appsheet_headers = {
        "Content-Type": "application/json",
        "ApplicationAccessKey": APPSHEET_API_KEY # Use the ApplicationAccessKey header
    }

    # Prepare the data payload for the AppSheet API.
    # The keys in the 'Rows' dictionary MUST exactly match your column names in AppSheet.
    appsheet_body = {
        "Action": "Edit", # We are editing an existing row
        "Properties": {}, # Add properties if needed (e.g., "Locale": "en-US")
        "Rows": [
            {
                # Use the unique row identifier received from the webhook to target the row
                "_RowNumber": row_id, # Or your actual unique key column name if not _RowNumber
                "Walgreens_Quantity": str(quantity), # Send quantity as string (safer) or convert to float/int if your column is Number
                "Walgreens_Status": status,
                "Last_Inventory_Check": datetime.datetime.now(datetime.timezone.utc).isoformat(), # Use ISO format for DateTime
                "API_Error_Message": error_msg if error_msg else "" # Update the error message column
            }
        ]
    }

    try:
        print(f"Calling AppSheet API to update row {row_id}...")
        appsheet_response = requests.post(
            appsheet_url,
            headers=appsheet_headers,
            json=appsheet_body,
            timeout=30 # Timeout for AppSheet API call
        )
        appsheet_response.raise_for_status() # Raise HTTPError for bad AppSheet API responses
        appsheet_result = appsheet_response.json()
        print(f"AppSheet API update successful for row {row_id}. Response: {appsheet_result}")

    except requests.exceptions.Timeout:
         print(f"AppSheet API request timed out while updating row {row_id}.")
    except requests.exceptions.RequestException as e:
        print(f"Error calling AppSheet API for row {row_id}: {e}")
        try: print(f"AppSheet API error response body: {appsheet_response.text}")
        except: pass # ignore if response body not accessible
    except Exception as e:
         print(f"Unexpected error during AppSheet API update attempt for row {row_id}: {e}")


# --- RunPod Entry Point ---
# When deploying on RunPod with Gunicorn or similar, this __name__ == '__main__':
# block is typically not executed directly by the server runner.
# The server runner (like Gunicorn) will import your 'app' instance.
if __name__ == '__main__':
    # This block is for local testing only.
    # You would set dummy environment variables here or use a .env file reader.
    # Example for local testing:
    # os.environ["WALGREENS_API_KEY"] = "YOUR_TEST_WALGREENS_KEY"
    # os.environ["WALGREENS_AFFILIATE_ID"] = "YOUR_TEST_AFFILIATE_ID"
    # os.environ["APPSHEET_API_KEY"] = "YOUR_TEST_APPSHEET_KEY"
    # os.environ["APPSHEET_APP_ID"] = "YOUR_TEST_APP_ID"
    # os.environ["APPSHEET_PRODUCT_TABLE_NAME"] = "YourProductTableName"
    # os.environ["WEBHOOK_SECRET"] = "YOUR_TEST_SECRET" # If testing security
    #
    # To test locally:
    # 1. Set the environment variables needed for local testing.
    # 2. Run this script: python your_script_name.py
    # 3. Use a tool like Postman or curl to send a POST request to http://127.0.0.1:5000/check_walgreens_inventory
    #    with a JSON body like:
    #    {"appsheet_row_id": "1", "product_id_18digit": "YOUR_TEST_PRODUCT_ID", "store_id": "YOUR_TEST_STORE_ID", "app_version": "1.0"}
    # app.run(debug=True, port=5000)
    pass # Keep this empty for RunPod deployment with Gunicorn