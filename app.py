from flask import Flask, request, jsonify
import requests
import os
import datetime
import json # Import json for potential raw response handling and parsing errors

app = Flask(__name__)

# --- Configuration (Load from Environment Variables!) ---
# These variables MUST be set in your RunPod endpoint/server settings.
# DO NOT hardcode your API keys or sensitive info directly in this file!
#
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
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET") # Load optional secret

# Use the Production URL based on the documentation
WALGREENS_API_URL = "https://services.walgreens.com/api/products/inventory/v4"

# --- Flask Endpoint to Receive AppSheet Webhook ---
# This route path must match the URL path you configure in your AppSheet webhook automation.
@app.route('/check_walgreens_inventory', methods=['POST'])
def check_walgreens_inventory():
    """
    Receives webhook from AppSheet containing product and store ID,
    calls Walgreens API to get store's full inventory dump,
    parses dump to find specific item, and updates AppSheet via its API.
    """
    # Optional: Implement webhook security check using a shared secret header
    if WEBHOOK_SECRET:
        auth_header = request.headers.get('X-Custom-Secret') # Use the header name you configured in AppSheet
        if auth_header != WEBHOOK_SECRET:
            print("Unauthorized webhook request: Invalid or missing secret header.")
            # Attempt to send error back to AppSheet if row_id is available from parsed data
            data = request.json # Try to parse even if auth fails to get row_id
            if data and data.get("appsheet_row_id"):
                 update_appsheet_status(data.get("appsheet_row_id"), quantity="Error", status="Webhook Auth Fail", error_msg="Invalid webhook secret.")
            # Use 401 Unauthorized HTTP status code for security failure
            return jsonify({"status": "error", "message": "Unauthorized"}), 401


    # Parse the incoming JSON payload from the AppSheet webhook
    try:
        data = request.get_json() # Use get_json() to handle potential parsing errors
    except Exception as e:
        print(f"Error parsing incoming JSON payload: {e}")
        return jsonify({"status": "error", "message": "Invalid JSON payload"}), 400

    # Check if payload is valid and contains necessary keys
    if not data:
        print("Empty JSON payload received from webhook.")
        return jsonify({"status": "error", "message": "Empty JSON payload"}), 400

    # Extract data sent from AppSheet webhook payload
    # These key names ("appsheet_row_id", "product_id_18digit", etc.)
    # MUST match the JSON keys you configured in your AppSheet webhook body!
    appsheet_row_id = data.get("appsheet_row_id")
    # This is the specific product ID we need to find *within* the Walgreens response dump
    product_id_18digit = data.get("product_id_18digit")
    store_id = data.get("store_id")
    app_version = data.get("app_version", "1.0") # Use provided version or default if not in payload

    # Basic validation of required fields from webhook payload
    if not all([appsheet_row_id, product_id_18digit, store_id]):
        missing_fields = [f for f in ["appsheet_row_id", "product_id_18digit", "store_id"] if data.get(f) is None]
        error_msg_payload = f"Missing required fields in webhook payload: {', '.join(missing_fields)}. Received: {data}"
        print(error_msg_payload)
        # Attempt to update AppSheet row with error status if row_id is present
        if appsheet_row_id:
             update_appsheet_status(appsheet_row_id, quantity="Error", status="Webhook Payload Fail", error_msg=error_msg_payload)
        return jsonify({"status": "error", "message": error_msg_payload}), 400

    print(f"Webhook received. Target Product ID: {product_id_18digit}, Store ID: {store_id}, AppSheet Row ID: {appsheet_row_id}")

    # Initialize variables to store results and status
    walgreens_quantity = "N/A" # Default if not found or error
    walgreens_status = "Checking Inventory..." # Initial status that might be set early in AppSheet
    error_message = None # To store any error messages encountered during the process
    raw_response_snippet = "" # Store a snippet of the raw response for debugging logs


    try:
        # --- 1. Call Walgreens Inventory API (Requesting full store inventory) ---
        # Based on verification, the API doesn't filter by item ID in the request body.
        # It requires these 4 parameters to dump the store's inventory.
        walgreens_payload = {
            "apiKey": WALGREENS_API_KEY,
            "affid": WALGREENS_AFFILIATE_ID,
            "store": store_id,
            "appVer": app_version,
            # DO NOT include the specific product_id_18digit here based on your verification
        }

        walgreens_headers = {
            "Content-Type": "application/json", # Docs specify JSON request format and curl example shows header
            "Accept": "application/json" # Request JSON response
            # No other headers seem required based on the v2 doc snippet
        }

        print(f"Calling Walgreens API for store {store_id} at {WALGREENS_API_URL}")
        # Use POST method as per documentation and curl example
        walgreens_response = requests.post(
            WALGREENS_API_URL,
            headers=walgreens_headers,
            json=walgreens_payload, # Send payload as JSON body
            timeout=60 # Set a reasonable timeout for the external API call
        )

        # Store a snippet of the response for logging/debugging, especially on errors
        raw_response_snippet = walgreens_response.text[:1000] + '...' if len(walgreens_response.text) > 1000 else walgreens_response.text
        print(f"Walgreens API Status Code: {walgreens_response.status_code}")
        # Uncomment below for detailed debugging of the raw response snippet
        # print(f"Walgreens API Response Snippet: {raw_response_snippet}")

        # Check for HTTP errors (4xx or 5xx status codes).
        # requests.exceptions.RequestException will be raised if status is bad (>=400).
        walgreens_response.raise_for_status()

        # Parse the JSON response. This will raise JSONDecodeError if the response is not valid JSON.
        walgreens_data = walgreens_response.json()

        # --- 2. Parse the Full Walgreens Inventory Dump to Find the Specific Item ---
        # Based on the actual response provided (a direct list of dictionaries like {"id": "...", "s": "...", "q": ...}).
        inventory_list = walgreens_data # The entire parsed JSON is the list of items

        # Ensure the parsed data is actually a list before trying to iterate
        if not isinstance(inventory_list, list):
             error_message = f"Walgreens API response was not a JSON list as expected. Type: {type(inventory_list)}. Raw: {raw_response_snippet}"
             walgreens_status = "Error: Unexpected Response Format"
             walgreens_quantity = "Error"
             print(error_message)
             # If it's not a list, we cannot find the item, so set found_item_data to None
             found_item_data = None
        else:
             # --- Search for the specific item within the list ---
             found_item_data = None
             # Iterate directly through the list items (each should be a dictionary)
             for item_data in inventory_list:
                 # Check if the current item is a dictionary and its 'id' key matches the target product ID
                 if isinstance(item_data, dict) and item_data.get("id") == product_id_18digit:
                     found_item_data = item_data # Found the item's inventory data dictionary
                     print(f"Found item {product_id_18digit} in Walgreens response dump.")
                     break # Stop searching once found

        # --- 3. Extract Quantity and Determine Status from Found Item ---
        if found_item_data:
            # Quantity is expected under key "q". Use .get() with a default for safety.
            walgreens_quantity = found_item_data.get("q", "N/A")

            # Extract update time if needed (key "ut") - it's epoch time in milliseconds
            # If you want to use this timestamp in AppSheet, uncomment the logic below,
            # add a corresponding column in AppSheet, and modify update_appsheet_status.
            # update_time_epoch_ms = found_item_data.get("ut")
            # walgreens_updated_timestamp = None # Initialize to None
            # if update_time_epoch_ms is not None:
            #      try:
            #          # Convert epoch milliseconds to seconds, then to datetime object (UTC)
            #          update_time_epoch_s = int(update_time_epoch_ms) / 1000.0 # Use 1000.0 for float division
            #          walgreens_updated_timestamp = datetime.datetime.fromtimestamp(update_time_epoch_s, datetime.timezone.utc)
            #      except (ValueError, TypeError, OSError) as ts_e: # Added OSError for potential invalid timestamp values
            #           print(f"Warning: Could not parse 'ut' epoch time '{update_time_epoch_ms}': {ts_e}")
            #           # walgreens_updated_timestamp remains None, will use current time or leave AppSheet column blank


            # Determine status based on quantity.
            # Ensure robust conversion of quantity to a number.
            try:
                quantity_num = 0 # Default numeric quantity to 0
                # Check if walgreens_quantity is a number type or a string representing a number
                if isinstance(walgreens_quantity, (int, float)):
                     quantity_num = int(walgreens_quantity) # Convert float to int if needed
                elif isinstance(walgreens_quantity, str) and walgreens_quantity.isdigit():
                     quantity_num = int(walgreens_quantity)
                # Else, quantity_num remains 0 (covers "N/A", "Error", empty string, etc.)

                if quantity_num > 0:
                    walgreens_status = "In Stock"
                else:
                     # Quantity is 0 or non-numeric/missing but item was found
                     walgreens_status = "Out of Stock"

            except Exception as q_e:
                 # Catch any errors during quantity conversion or status determination logic
                 print(f"Error determining status from quantity '{walgreens_quantity}': {q_e}")
                 walgreens_status = "Quantity Data Issue" # Status indicating problem with quantity value


        # --- Item was not found in the list after iterating OR parsing failed before searching ---
        # Check if found_item_data is None AND there wasn't a previous parsing format error
        elif error_message is None:
            walgreens_quantity = "N/A" # Quantity is N/A because the item wasn't found
            walgreens_status = "Item Not Listed at Store" # Specific status for item not found in dump
            error_message = f"Product ID {product_id_18digit} not found in the inventory dump for store {store_id}."
            print(error_message)


    # --- Error Handling Blocks ---
    # These catch specific exceptions that occur during the API call or initial response processing
    except requests.exceptions.Timeout:
        # Catches if the request to Walgreens API took too long
        error_message = "Walgreens API request timed out."
        walgreens_status = "Error: Timeout" # Status for AppSheet
        walgreens_quantity = "Error" # Quantity for AppSheet
        print(error_message)
    except requests.exceptions.RequestException as e:
        # This catches non-200 HTTP responses from Walgreens API (e.g., 40x, 50x)
        # and other request-related errors (like network issues before getting a response)
        status_code = walgreens_response.status_code if 'walgreens_response' in locals() else 'N/A'
        error_message = f"Walgreens API HTTP or request error: {e}. Status: {status_code}. Raw: {raw_response_snippet}"
        walgreens_status = f"Error: HTTP {status_code}"
        walgreens_quantity = "Error"
        print(error_message)
    except json.JSONDecodeError:
        # Catch errors if the response body is not valid JSON, despite getting a 200 OK or other status
        error_message = f"Walgreens API response is not valid JSON. Raw: {raw_response_snippet}"
        walgreens_status = "Error: Invalid JSON Response"
        walgreens_quantity = "Error"
        print(error_message)
    except Exception as e:
        # Catch any other unexpected errors that might occur within the try block
        error_message = f"An unexpected error occurred during processing: {e}. Raw response snippet: {raw_response_snippet}"
        walgreens_status = "Error: Processing Failed"
        walgreens_quantity = "Error"
        print(error_message)

    finally:
        # --- Call AppSheet via API (This block always runs after try/except/else) ---
        # This ensures that the AppSheet row is updated regardless of the outcome of the Walgreens API call.
        # The status, quantity, and error_message variables will reflect the specific outcome.
        print(f"Attempting to update AppSheet row: {appsheet_row_id} with Quantity='{walgreens_quantity}', Status='{walgreens_status}', Error='{error_message}'")
        # Call the helper function to update the specific row in AppSheet
        update_appsheet_status(
            row_id=appsheet_row_id,
            quantity=walgreens_quantity,
            status=walgreens_status,
            error_msg=error_message
            # If you uncommented the 'ut' parsing, pass the walgreens_updated_timestamp here
            # walgreens_updated_timestamp=walgreens_updated_timestamp # Example
        )

    # Return a success response to the AppSheet webhook.
    # AppSheet expects a 200 status code to consider the webhook itself successful,
    # even if the Walgreens API call or internal processing resulted in an 'Error' status
    # that will be reported back in the AppSheet row update.
    return jsonify({"status": "processing_initiated", "message": "Walgreens inventory check process started.", "row_id": appsheet_row_id}), 200


# --- Helper Function to Update AppSheet Row via API ---
# This function runs asynchronously relative to the initial webhook response
# due to how Flask/Gunicorn handles requests, but is called synchronously
# within the main check_walgreens_inventory function's finally block.
def update_appsheet_status(row_id, quantity, status, error_msg=None, walgreens_updated_timestamp=None):
    """
    Calls the AppSheet API to update a specific row in the product table
    with the results of the inventory check.
    """
    # Check for necessary configuration before attempting to call AppSheet API
    if not all([APPSHEET_APP_ID, APPSHEET_PRODUCT_TABLE_NAME, APPSHEET_API_KEY]):
         print("Fatal Error: AppSheet API configuration missing (App ID, Table Name, or API Key). Cannot update AppSheet row.")
         # This error won't be reported back to the specific row without config
         return

    if not row_id:
        print("Fatal Error: Cannot update AppSheet row - row_id is missing.")
        return # Cannot update if we don't know which row to update


    appsheet_url = f"https://api.appsheet.com/api/v2/apps/{APPSHEET_APP_ID}/tables/{APPSHEET_PRODUCT_TABLE_NAME}/Action"
    appsheet_headers = {
        "Content-Type": "application/json",
        "ApplicationAccessKey": APPSHEET_API_KEY # Use the ApplicationAccessKey header for authentication
    }

    # Determine the timestamp to send to AppSheet's Last_Inventory_Check column
    # Use the Walgreens update time if available and parsed, otherwise use the current time
    timestamp_to_send = datetime.datetime.now(datetime.timezone.utc)
    # If you added walgreens_updated_timestamp parameter and parsed it:
    # if isinstance(walgreens_updated_timestamp, datetime.datetime):
    #    timestamp_to_send = walgreens_updated_timestamp

    # Format the timestamp as an ISO 8601 string (required by AppSheet DateTime columns)
    timestamp_iso_format = timestamp_to_send.isoformat()


    # Prepare the data payload for the AppSheet API.
    # The keys in the 'Rows' dictionary MUST exactly match your column names in AppSheet.
    # Ensure data types are compatible (e.g., sending a string for a Text column,
    # an ISO string for a DateTime column).
    appsheet_body = {
        "Action": "Edit", # Use the "Edit" action to update an existing row
        "Properties": {
            # Add any necessary properties for the API call, e.g., "Locale": "en-US"
            # "Locale": "en-US"
        },
        "Rows": [
            {
                # Use the unique row identifier received from the webhook to target the row
                # Ensure "_RowNumber" matches your actual unique key column name in AppSheet if it's different!
                "_RowNumber": row_id,
                # Convert quantity to string for flexibility (handles "N/A", "Error")
                # If your AppSheet quantity column is strictly Number, you might need conversion logic here
                # or handle "N/A" / "Error" statuses differently.
                "Walgreens_Quantity": str(quantity),
                "Walgreens_Status": status,
                "Last_Inventory_Check": timestamp_iso_format, # Use the determined timestamp
                "API_Error_Message": error_msg if error_msg else "" # Ensure error_msg is a string or empty string
                # If you added a column for Walgreens update time:
                # "Walgreens_Last_Updated": walgreens_updated_timestamp.isoformat() if isinstance(walgreens_updated_timestamp, datetime.datetime) else "" # Example
            }
        ]
    }

    try:
        print(f"Calling AppSheet API to update row {row_id}...")
        # Send the update request to the AppSheet API endpoint
        appsheet_response = requests.post(
            appsheet_url,
            headers=appsheet_headers,
            json=appsheet_body, # Send payload as JSON body
            timeout=30 # Set a timeout for the AppSheet API call
        )
        # Raise an HTTPError for bad responses (4xx or 5xx) from AppSheet API
        appsheet_response.raise_for_status()
        appsheet_result = appsheet_response.json() # Parse the JSON response from AppSheet API
        print(f"AppSheet API update successful for row {row_id}. Response: {appsheet_result}")

    except requests.exceptions.Timeout:
         print(f"AppSheet API request timed out while updating row {row_id}.")
    except requests.exceptions.RequestException as e:
        # Catch HTTP errors from AppSheet API or other request issues
        print(f"Error calling AppSheet API for row {row_id}: {e}")
        try:
            # Attempt to print the response body for debugging AppSheet API errors
            print(f"AppSheet API error response body: {appsheet_response.text}")
        except:
             pass # ignore if response body not accessible
    except Exception as e:
         # Catch any other unexpected errors during the AppSheet API update attempt
         print(f"Unexpected error during AppSheet API update attempt for row {row_id}: {e}")


# --- Gunicorn/RunPod Entry Point ---
# When deploying on RunPod using Gunicorn or a similar server runner,
# this __name__ == '__main__': block is typically NOT executed.
# The server runner imports your 'app' instance directly.
# Keep this block empty for production deployment via Gunicorn/RunPod Endpoint.
if __name__ == '__main__':
    # This block is ONLY for running the Flask development server locally for testing.
    # To test locally:
    # 1. Set the necessary environment variables in your local shell or a .env file loader.
    #    (e.g., export WALGREENS_API_KEY="...", etc.)
    # 2. Run this script from your terminal: python your_script_name.py
    # 3. Use a tool like Postman or curl to send a POST request to http://127.0.0.1:5000/check_walgreens_inventory
    #    with a JSON body like:
    #    {"appsheet_row_id": "YOUR_TEST_ROW_ID", "product_id_18digit": "YOUR_TEST_PRODUCT_ID", "store_id": "YOUR_TEST_STORE_ID", "app_version": "1.0"}
    #
    # print("Starting Flask development server (for local testing only)...")
    # app.run(debug=True, port=5000)
    pass # Keep this empty for production deployment via Gunicorn/RunPod