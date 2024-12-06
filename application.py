from flask import Flask, request, redirect, jsonify
import requests
from requests.auth import HTTPBasicAuth
import urllib.parse
import json

app = Flask(__name__)

# Delhivery API details
DELHIVERY_API_URL = "https://track.delhivery.com/api/v1/packages/json/"
DELHIVERY_API_KEY = "5a7b27e8d938067996dbacce89a50cf909598111"

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
            if order_status == 'completed':
                # Call Delhivery API to get AWB number for the completed order
                awb_response = get_awb_number(order_id)
                if awb_response:
                    awb_number = awb_response.get('awb_number')
                    if awb_number:
                        # Prepare order details with AWB number and tracking URL
                        tracking_url = f"https://www.delhivery.com/track?awb={awb_number}"
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
    params = {
        'waybill': order_id,
    }
    headers = {
        'Authorization': f'Bearer {DELHIVERY_API_KEY}'
    }

    try:
        # Make the GET request to the Delhivery API
        response = requests.get(DELHIVERY_API_URL, params=params, headers=headers)
        
        if response.status_code == 200:
            data = response.json()
            # Check if we have an AWB number in the response
            if 'awb_number' in data:
                return data
            else:
                return None
        else:
            # Handle non-200 status codes
            return None
    except requests.exceptions.RequestException as e:
        # Handle any errors that occur during the API request
        print(f"Error calling Delhivery API: {e}")
        return None


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0')
