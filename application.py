from flask import Flask, request, redirect, jsonify
import requests
from requests.auth import HTTPBasicAuth
import urllib.parse
import json

app = Flask(__name__)

# Delhivery API details
DELHIVERY_API_URL = "https://track.delhivery.com/api/v1/packages/json/"
DELHIVERY_API_KEY = "a4d484e7d39015a655fd6b3c6c10152adf7a49c5"

# Shiprocket API details
SHIPROCKET_API_URL = "https://apiv2.shiprocket.in/v1/external/courier/track"
SHIPROCKET_TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOjU1MzA4MjMsInNvdXJjZSI6InNyLWF1dGgtaW50IiwiZXhwIjoxNzQzNjA4MzMyLCJqdGkiOiIzdndTN25uWEpZdlhWM2FoIiwiaWF0IjoxNzQyNzQ0MzMyLCJpc3MiOiJodHRwczovL3NyLWF1dGguc2hpcHJvY2tldC5pbi9hdXRob3JpemUvdXNlciIsIm5iZiI6MTc0Mjc0NDMzMiwiY2lkIjozNzgyMDE2LCJ0YyI6MzYwLCJ2ZXJib3NlIjpmYWxzZSwidmVuZG9yX2lkIjowLCJ2ZW5kb3JfY29kZSI6Indvb2NvbW1lcmNlIn0.edcLdphgL7izCI2elwLI-vjzP-zK2vreoSSOA332qvI"  # Replace with your actual token

# WooCommerce API credentials
WOOCOMMERCE_URL = "X"
CONSUMER_KEY = "X"
CONSUMER_SECRET = "X"

@app.route('/check-woo', methods=['GET'])
def check_woo():
    WOOCOMMERCE_URL = "X"
    CONSUMER_KEY = "X"
    CONSUMER_SECRET = "X"
    """Retrieve order status from WooCommerce and check if shipped. If shipped, fetch AWB from Delhivery or Shiprocket."""
    order_id = request.args.get('order-id')
    type = request.args.get('type')
    if type == 'F':
        WOOCOMMERCE_URL = "https://figureshub.in/wp-json/wc/v3"
        CONSUMER_KEY = "ck_adf3760d0edad5ed2878b3098259457b14da15f1"
        CONSUMER_SECRET = "cs_be0f2a6b00625d5a90e770711aa7aef8823de913"
      
    elif type == 'T':
        WOOCOMMERCE_URL = "https://tcghub.in/wp-json/wc/v3"
        CONSUMER_KEY = "ck_0ce7629909f34c95a40f008d48e6c9262df56daa"
        CONSUMER_SECRET = "cs_09b302e2e1c9e7e944b30751bf99287bb29db770"
      
    if not order_id:
        return "Missing 'order-id' parameter!", 400

    try:
        # Call WooCommerce API to fetch order details
        response = requests.get(
            f"{WOOCOMMERCE_URL}/orders/{order_id}",
            auth=HTTPBasicAuth(CONSUMER_KEY, CONSUMER_SECRET)
        )

        # Handle response
        if response.status_code == 200:
            order_data = response.json()

            # Check if order is completed
            order_status = order_data.get('status', 'Unknown')
            if order_status == 'completed' or order_status == 'processing':
                # Try to get AWB from Delhivery first
                awb_response = None
                awb_number = None
                tracking_url = ""
                
                if order_status == 'completed':
                    # First try Delhivery API
                    delhivery_response = get_awb_number(order_id)
                    if delhivery_response is not None:
                        if delhivery_response and delhivery_response.get('awb_number'):
                            # Delhivery AWB found
                            awb_response = delhivery_response
                            awb_number = awb_response.get('awb_number')
                            tracking_url = f"https://www.delhivery.com/track-v2/package/{awb_number}"
                    else:
                        # Delhivery AWB not found, try Shiprocket
                        shiprocket_response = get_shiprocket_tracking(order_id, channel_id)
                        
                        if shiprocket_response:
                            awb_response = shiprocket_response
                            awb_number = awb_response.get('awb_number')
                            tracking_url = awb_response.get('tracking_url')
                else:
                    # For processing orders
                    awb_response = "1234"
                    awb_number = "1234"
                    tracking_url = ""
                
                # Check if we have tracking details from either Delhivery or Shiprocket
                if awb_number:
                    # Prepare order details with AWB number and tracking URL
                    json_data = {
                        "order_id": order_id,
                        "status": order_status,
                        "total": order_data.get('total', 'Unknown'),
                        "date_created": order_data.get('date_created', 'Unknown').split('T')[0],  # Date only
                        "items": [
                            {"name": item['name'], "quantity": item['quantity']}
                            for item in order_data.get('line_items', [])
                        ],
                        "awb_number": awb_number,
                        "tracking_url": tracking_url
                    }
                    # Convert to JSON string and URL encode
                    json_str = json.dumps(json_data)
                    encoded_json = urllib.parse.quote(json_str)

                    # Redirect to the appropriate page
                    if order_status == "completed":
                        if type=='F':
                            return redirect(f'https://figureshub.in/order-shipped/?order-data={encoded_json}')
                        else:
                            return redirect(f'https://tcghub.in/order-shipped/?order-data={encoded_json}')
                    else:
                        if type=='F':
                            return redirect(f'https://figureshub.in/order-packing/?order-data={encoded_json}')
                        else:
                            return redirect(f'https://tcghub.in/order-packing/?order-data={encoded_json}')
                else:
                    # No tracking info from either service
                    return "AWB number not found for the order in either Delhivery or Shiprocket.", 404
            else:
                # Order not completed, redirect to a different page
                if type=='F':
                    return redirect(f'https://figureshub.in/your-order-is-getting-packed/?order-id={order_id}')
                else:
                    return redirect(f'https://tcghub.in/no-order/?order-id={order_id}')
        
        elif response.status_code == 404:
            if type=='F':
                return redirect(f'https://figureshub.in/your-order-is-getting-packed/?order-id={order_id}')
            else:
                return redirect(f'https://tcghub.in/no-order/?order-id={order_id}')
        else:
            return f"Error retrieving order details: {response.status_code}, {response.text}", 500

    except Exception as e:
        return f"An error occurred: {e}", 500


def get_awb_number(order_id):
    """Call the Delhivery API to get the AWB number for the given order ID."""
    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Token {DELHIVERY_API_KEY}',  # Pass the API key as a Token
    }

    try:
        # Construct the API URL with the waybill parameter
        api_url = f"{DELHIVERY_API_URL}?waybill=&ref_ids={order_id}"
        print(f"Making request to Delhivery API: {api_url}")  # Debugging log

        # Make the GET request to the Delhivery API with headers
        response = requests.get(api_url, headers=headers)

        # Log response status and body for debugging
        print(f"Delhivery API response code: {response.status_code}")
        print(f"Delhivery API response body: {response.text}")

        if response.status_code == 200:
            data = response.json()

            # Check if 'ShipmentData' exists and contains data
            shipment_data = data.get('ShipmentData', [])
            if shipment_data:
                # Access the AWB number from 'Shipment' directly
                awb_number = shipment_data[0].get('Shipment', {}).get('AWB')
                if awb_number:
                    return {'awb_number': awb_number}
                else:
                    print("No AWB number found in Shipment.")
                    return None
            else:
                print("No ShipmentData found in the response.")
                return None
        else:
            # Handle non-200 status codes
            print(f"Error: {response.status_code}, {response.text}")
            return None
    except requests.exceptions.RequestException as e:
        # Handle any errors that occur during the API request
        print(f"Error calling Delhivery API: {e}")
        return None


def get_shiprocket_tracking(order_id, channel_id=None):
    """Call the Shiprocket API to get tracking information for the given order ID."""
    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {SHIPROCKET_TOKEN}'
    }

    try:
        # Construct the API URL with parameters
        api_url = f"{SHIPROCKET_API_URL}?order_id={order_id}"
        if channel_id:
            api_url += f"&channel_id={channel_id}"
            
        print(f"Making request to Shiprocket API: {api_url}")  # Debugging log

        # Make the GET request to the Shiprocket API
        response = requests.get(api_url, headers=headers)

        # Log response status and body for debugging
        print(f"Shiprocket API response code: {response.status_code}")
        print(f"Shiprocket API response body: {response.text}")

        if response.status_code == 200:
            data = response.json()
            
            # Check if we have tracking data
            if data and isinstance(data, list) and len(data) > 0:
                tracking_data = data[0].get('tracking_data', {})
                
                # Get shipment track information
                shipment_track = tracking_data.get('shipment_track', [])
                if shipment_track and len(shipment_track) > 0:
                    # Extract AWB code
                    awb_number = shipment_track[0].get('awb_code')
                    # Get tracking URL
                    tracking_url = tracking_data.get('track_url', f"https://shiprocket.co/tracking/{awb_number}")
                    
                    if awb_number:
                        return {
                            'awb_number': awb_number,
                            'tracking_url': tracking_url
                        }
            
            print("No tracking data found in Shiprocket response.")
            return None
        else:
            # Handle non-200 status codes
            print(f"Error: {response.status_code}, {response.text}")
            return None
    except requests.exceptions.RequestException as e:
        # Handle any errors that occur during the API request
        print(f"Error calling Shiprocket API: {e}")
        return None


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0')