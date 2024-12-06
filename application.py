from flask import Flask, request, redirect, jsonify
import requests
from requests.auth import HTTPBasicAuth
import urllib.parse
import json

app = Flask(__name__)

# Delhivery API details
DELHIVERY_API_URL = "https://track.delhivery.com/api/v1/packages/json/"
DELHIVERY_API_KEY = "a4d484e7d39015a655fd6b3c6c10152adf7a49c5"

# WooCommerce API credentials
WOOCOMMERCE_URL = "https://figureshub.in/wp-json/wc/v3"
CONSUMER_KEY = "ck_adf3760d0edad5ed2878b3098259457b14da15f1"
CONSUMER_SECRET = "cs_be0f2a6b00625d5a90e770711aa7aef8823de913"

@app.route('/check-woo', methods=['GET'])
def check_woo():
    """Retrieve order status from WooCommerce and check if shipped. If shipped, fetch AWB from Delhivery."""
    order_id = request.args.get('order-id')
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
                # Call Delhivery API to get AWB number for the completed order
                awb_response=""
                if order_status == 'completed': 
                    awb_response = get_awb_number(order_id)
                    awb_number = awb_response.get('awb_number')
                    tracking_url = f"https://www.delhivery.com/track-v2/package/{awb_number}"
                else:
                    awb_response = "1234"
                    awb_number = "1234"
                    tracking_url = ""
                if awb_response:
                    
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

                        # Redirect to the success page with the encoded JSON data in the URL
                        return redirect(f'https://figureshub.in/order-shipped/?order-data={encoded_json}')
                    else:
                        return "AWB number not found for the order.", 404
                else:
                    return "Error fetching AWB from Delhivery API.", 500
            else:
                # Order not completed, redirect to a different page
                return redirect(f'https://figureshub.in/your-order-is-getting-packed/?order-id={order_id}')
        
        elif response.status_code == 404:
            return redirect(f'https://figureshub.in/your-order-is-getting-packed/?order-id={order_id}')
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


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0')
