from flask import Flask, request, redirect, jsonify
import requests
from requests.auth import HTTPBasicAuth
import urllib.parse
import json
import logging

# Set up logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Delhivery API details
DELHIVERY_API_URL = "https://track.delhivery.com/api/v1/packages/json/"
DELHIVERY_API_KEY = "a4d484e7d39015a655fd6b3c6c10152adf7a49c5"

# Shiprocket API details
SHIPROCKET_API_URL = "https://apiv2.shiprocket.in/v1/external/courier/track"
SHIPROCKET_TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOjcwNDYyOTcsInNvdXJjZSI6InNyLWF1dGgtaW50IiwiZXhwIjoxNzUyMDc1MzIzLCJqdGkiOiJNeVVNdjBIYUV0N1ZPOWZRIiwiaWF0IjoxNzUxMjExMzIzLCJpc3MiOiJodHRwczovL3NyLWF1dGguc2hpcHJvY2tldC5pbi9hdXRob3JpemUvdXNlciIsIm5iZiI6MTc1MTIxMTMyMywiY2lkIjozNzgyMDE2LCJ0YyI6MzYwLCJ2ZXJib3NlIjpmYWxzZSwidmVuZG9yX2lkIjowLCJ2ZW5kb3JfY29kZSI6Indvb2NvbW1lcmNlIn0.S7g5up3ntlfE9app-XU550Ss6rtLQNInTRSKvkL1TsQ"
@app.route('/check-woo', methods=['GET'])
def check_woo():
    """Retrieve order status from WooCommerce and check if shipped. If shipped, fetch AWB from Delhivery or Shiprocket."""
    order_id = request.args.get('order-id')
    type_param = request.args.get('type')
    
    logger.info(f"Received request for order ID: {order_id}, type: {type_param}")
    
    # Set WooCommerce credentials based on site type
    if type_param == 'F':
        woocommerce_url = "https://figureshub.in/wp-json/wc/v3"
        consumer_key = "ck_adf3760d0edad5ed2878b3098259457b14da15f1"
        consumer_secret = "cs_be0f2a6b00625d5a90e770711aa7aef8823de913"
        base_url = "https://figureshub.in"
    elif type_param == 'T':
        woocommerce_url = "https://tcghub.in/wp-json/wc/v3"
        consumer_key = "ck_0ce7629909f34c95a40f008d48e6c9262df56daa"
        consumer_secret = "cs_09b302e2e1c9e7e944b30751bf99287bb29db770"
        base_url = "https://tcghub.in"
    else:
        return "Invalid 'type' parameter. Must be 'F' or 'T'.", 400
      
    if not order_id:
        return "Missing 'order-id' parameter!", 400

    try:
        # Call WooCommerce API to fetch order details
        logger.info(f"Calling WooCommerce API for order {order_id}")
        response = requests.get(
            f"{woocommerce_url}/orders/{order_id}",
            auth=HTTPBasicAuth(consumer_key, consumer_secret)
        )

        # Handle response
        if response.status_code == 200:
            order_data = response.json()
            logger.info(f"WooCommerce API returned data for order {order_id}")

            # Check if order is completed or processing
            order_status = order_data.get('status', 'Unknown')
            logger.info(f"Order status: {order_status}")
            
            if order_status == 'completed' or order_status == 'processing':
                # Initialize tracking variables
                awb_number = None
                tracking_url = ""
                
                if order_status == 'completed':
                    try:
                        # First try Delhivery API
                        logger.info(f"Trying Delhivery API for order {order_id}")
                        delhivery_response = get_awb_number(order_id)
                        
                        if delhivery_response is not None and isinstance(delhivery_response, dict) and delhivery_response.get('awb_number'):
                            # Delhivery AWB found
                            awb_number = delhivery_response.get('awb_number')
                            tracking_url = f"https://www.delhivery.com/track-v2/package/{awb_number}"
                            logger.info(f"Found Delhivery AWB: {awb_number}")
                        else:
                            # Delhivery AWB not found, try Shiprocket
                            logger.info(f"No Delhivery AWB found, trying Shiprocket for order {order_id}")
                            try:
                                shiprocket_response = get_shiprocket_tracking(order_id)
                                
                                if shiprocket_response is not None and isinstance(shiprocket_response, dict) and shiprocket_response.get('awb_number'):
                                    awb_number = shiprocket_response.get('awb_number')
                                    tracking_url = shiprocket_response.get('tracking_url', f"https://shiprocket.co/tracking/{awb_number}")
                                    logger.info(f"Found Shiprocket AWB: {awb_number}")
                                else:
                                    logger.warning(f"No Shiprocket tracking info found for order {order_id}")
                            except Exception as e:
                                logger.error(f"Error fetching Shiprocket tracking: {e}")
                    except Exception as e:
                        logger.error(f"Error during shipping API calls: {e}")
                else:
                    # For processing orders
                    awb_number = "1234"
                    tracking_url = ""
                    logger.info("Order is in processing status, using placeholder AWB")
                
                # Prepare order details for JSON
                try:
                    items = []
                    for item in order_data.get('line_items', []):
                        items.append({
                            "name": item.get('name', 'Unknown Item'),
                            "quantity": item.get('quantity', 0)
                        })
                    
                    json_data = {
                        "order_id": order_id,
                        "status": order_status,
                        "total": order_data.get('total', 'Unknown'),
                        "date_created": order_data.get('date_created', 'Unknown').split('T')[0] if order_data.get('date_created') else 'Unknown',
                        "items": items,
                        "awb_number": awb_number if awb_number else "",
                        "tracking_url": tracking_url
                    }
                    
                    # Convert to JSON string and URL encode
                    json_str = json.dumps(json_data)
                    encoded_json = urllib.parse.quote(json_str)
                    
                    # Determine redirect URL based on status and AWB availability
                    if awb_number:
                        if order_status == "completed":
                            redirect_url = f'{base_url}/order-shipped/?order-data={encoded_json}'
                        else:
                            redirect_url = f'{base_url}/order-packing/?order-data={encoded_json}'
                    else:
                        redirect_url = f'{base_url}/your-order-is-getting-packed/?order-id={order_id}'
                        
                    logger.info(f"Redirecting to: {redirect_url}")
                    return redirect(redirect_url)
                    
                except Exception as e:
                    logger.error(f"Error preparing JSON data: {e}")
                    return redirect(f'{base_url}/your-order-is-getting-packed/?order-id={order_id}')
            else:
                # Order not completed or processing
                logger.info(f"Order not in completed/processing status, redirecting to appropriate page")
                if type_param == 'F':
                    return redirect(f'https://figureshub.in/your-order-is-getting-packed/?order-id={order_id}')
                else:
                    return redirect(f'https://tcghub.in/no-order/?order-id={order_id}')
        
        elif response.status_code == 404:
            logger.warning(f"Order {order_id} not found in WooCommerce")
            if type_param == 'F':
                return redirect(f'https://figureshub.in/your-order-is-getting-packed/?order-id={order_id}')
            else:
                return redirect(f'https://tcghub.in/no-order/?order-id={order_id}')
        else:
            logger.error(f"WooCommerce API error: {response.status_code}, {response.text}")
            return f"Error retrieving order details: {response.status_code}, {response.text}", 500

    except Exception as e:
        logger.error(f"General error in check_woo: {e}", exc_info=True)
        # Safe fallback
        if type_param == 'F':
            return redirect(f'https://figureshub.in/your-order-is-getting-packed/?order-id={order_id}')
        else:
            return redirect(f'https://tcghub.in/no-order/?order-id={order_id}')


def get_awb_number(order_id):
    """Call the Delhivery API to get the AWB number for the given order ID."""
    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Token {DELHIVERY_API_KEY}',
    }

    try:
        # Construct the API URL with the waybill parameter
        api_url = f"{DELHIVERY_API_URL}?waybill=&ref_ids={order_id}"
        logger.debug(f"Making request to Delhivery API: {api_url}")

        # Make the GET request to the Delhivery API with headers
        response = requests.get(api_url, headers=headers, timeout=10)

        # Log response status and body for debugging
        logger.debug(f"Delhivery API response code: {response.status_code}")
        logger.debug(f"Delhivery API response body: {response.text[:500]}...")  # Log only first 500 chars

        if response.status_code == 200:
            try:
                data = response.json()
                
                # Check if 'ShipmentData' exists and contains data
                shipment_data = data.get('ShipmentData', [])
                if shipment_data and len(shipment_data) > 0:
                    # Access the AWB number from 'Shipment' directly
                    shipment = shipment_data[0].get('Shipment', {})
                    if shipment and isinstance(shipment, dict):
                        awb_number = shipment.get('AWB')
                        if awb_number:
                            return {'awb_number': awb_number}
                
                logger.warning("No valid AWB data found in Delhivery response")
                return None
            except json.JSONDecodeError:
                logger.error("Failed to parse Delhivery API response as JSON")
                return None
        else:
            # Handle non-200 status codes
            logger.warning(f"Delhivery API error: {response.status_code}")
            return None
    except requests.exceptions.RequestException as e:
        # Handle any errors that occur during the API request
        logger.error(f"Error calling Delhivery API: {e}")
        return None
    except Exception as e:
        logger.error(f"Unexpected error in get_awb_number: {e}")
        return None


def get_shiprocket_tracking(order_id):
    """Call the Shiprocket API to get tracking information for the given order ID."""
    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {SHIPROCKET_TOKEN}'
    }

    try:
        # Construct the API URL with parameters
        api_url = f"{SHIPROCKET_API_URL}?order_id={order_id}"
        logger.debug(f"Making request to Shiprocket API: {api_url}")

        # Make the GET request to the Shiprocket API
        response = requests.get(api_url, headers=headers, timeout=10)

        # Log response status and body for debugging
        logger.debug(f"Shiprocket API response code: {response.status_code}")
        logger.debug(f"Shiprocket API response body: {response.text[:500]}...")  # Log only first 500 chars

        if response.status_code == 200:
            try:
                data = response.json()
                
                # Make sure we have a list with at least one item
                if data and isinstance(data, list) and len(data) > 0:
                    tracking_data = data[0].get('tracking_data', {})
                    if not tracking_data or not isinstance(tracking_data, dict):
                        logger.warning("Tracking data not found or not a dict in Shiprocket response")
                        return None
                    
                    # Get shipment track information
                    shipment_track = tracking_data.get('shipment_track', [])
                    if shipment_track and isinstance(shipment_track, list) and len(shipment_track) > 0:
                        # Extract AWB code
                        awb_number = shipment_track[0].get('awb_code')
                        # Get tracking URL
                        tracking_url = tracking_data.get('track_url')
                        
                        if not tracking_url and awb_number:
                            tracking_url = f"https://shiprocket.co/tracking/{awb_number}"
                        
                        if awb_number:
                            return {
                                'awb_number': awb_number,
                                'tracking_url': tracking_url
                            }
                
                logger.warning("No valid tracking data found in Shiprocket response")
                return None
            except json.JSONDecodeError:
                logger.error("Failed to parse Shiprocket API response as JSON")
                return None
        else:
            # Handle non-200 status codes
            logger.warning(f"Shiprocket API error: {response.status_code}")
            return None
    except requests.exceptions.RequestException as e:
        # Handle any errors that occur during the API request
        logger.error(f"Error calling Shiprocket API: {e}")
        return None
    except Exception as e:
        logger.error(f"Unexpected error in get_shiprocket_tracking: {e}")
        return None


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0')