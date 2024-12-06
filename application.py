from flask import Flask, request, redirect, jsonify
import requests
from requests.auth import HTTPBasicAuth
import urllib.parse

app = Flask(__name__)

# WooCommerce API credentials
WOOCOMMERCE_URL = "https://figureshub.in/wp-json/wc/v3"
CONSUMER_KEY = "ck_adf3760d0edad5ed2878b3098259457b14da15f1"
CONSUMER_SECRET = "cs_be0f2a6b00625d5a90e770711aa7aef8823de913"
@app.route('/check-woo', methods=['GET'])
def check_woo():
    """Retrieve order status and other details from WooCommerce and redirect with JSON data."""
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
            # Format the date and remove time part
            order_date = order_data.get('date_created', 'Unknown').split('T')[0]

            # Construct the order details
            json_data = {
                "order_id": order_id,
                "status": order_data.get('status', 'Unknown'),
                "total": order_data.get('total', 'Unknown'),
                "date_created": order_date,
                "items": [
                    {
                        "name": item.get('name', 'Unknown'),
                        "quantity": item.get('quantity', 'Unknown')
                    }
                    for item in order_data.get('line_items', [])
                ]
            }
            # Convert JSON to a string and encode it for URL
            encoded_json = urllib.parse.quote(str(json_data))
            return redirect(f'https://figureshub.in/order-shipped/?order-data={encoded_json}')
        elif response.status_code == 404:
            return redirect(f'https://figureshub.in/your-order-is-getting-packed/?order-id={order_id}')
        else:
            return f"Error retrieving order details: {response.status_code}, {response.text}", 500
    except Exception as e:
        return f"An error occurred: {e}", 500

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0')
