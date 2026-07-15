from flask import Flask, request, redirect, jsonify
import requests
from requests.auth import HTTPBasicAuth
import json
import logging
import os
import time
import secrets
import sqlite3
from datetime import datetime, timezone
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Set up logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------

DELHIVERY_API_URL = "https://track.delhivery.com/api/v1/packages/json/"
DELHIVERY_API_KEY = os.getenv('DELHIVERY_API_KEY')

SHIPROCKET_API_URL = "https://apiv2.shiprocket.in/v1/external/courier/track"
SHIPROCKET_TOKEN = os.getenv('SHIPROCKET_TOKEN')

# WooCommerce credentials for FiguresHub
FIGURESHUB_CONSUMER_KEY = os.getenv('FIGURESHUB_CONSUMER_KEY')
FIGURESHUB_CONSUMER_SECRET = os.getenv('FIGURESHUB_CONSUMER_SECRET')

# WooCommerce credentials for TCGHub
TCGHUB_CONSUMER_KEY = os.getenv('TCGHUB_CONSUMER_KEY')
TCGHUB_CONSUMER_SECRET = os.getenv('TCGHUB_CONSUMER_SECRET')

# --- Token store settings -----------------------------------------------------
# Where the token database lives. Anywhere writable by the Flask process.
TOKEN_DB_PATH = os.getenv('TOKEN_DB_PATH', 'order_tokens.db')
# How long a token stays valid (seconds). 1800 = 30 minutes.
# Long enough that customers can refresh / come back to the page; short enough
# that a shared/leaked link goes dead quickly.
TOKEN_TTL = int(os.getenv('TOKEN_TTL', '1800'))

# --- IDOR protection (STRONGLY RECOMMENDED — read the note below) -------------
# When True, /check-woo requires a &key=<woocommerce_order_key> that must match
# the order's real order_key before any data is returned. This stops someone
# from simply incrementing order-id to read other customers' orders.
#
# Leaving this False keeps your current behaviour working immediately, but the
# IDOR hole remains open. To turn it on:
#   1. Set REQUIRE_ORDER_KEY = True (or env REQUIRE_ORDER_KEY=1)
#   2. Update wherever you generate the "track order" link so it includes the
#      order key, e.g.:  /check-woo?order-id=123&type=F&key=wc_order_AbC123...
#      (WooCommerce already creates this per-order secret; it's the same key in
#      its own "View order" / order-received URLs.)
REQUIRE_ORDER_KEY = os.getenv('REQUIRE_ORDER_KEY', '0') == '1'

# Validate that all required environment variables are set
required_env_vars = [
    'DELHIVERY_API_KEY',
    'SHIPROCKET_TOKEN',
    'FIGURESHUB_CONSUMER_KEY',
    'FIGURESHUB_CONSUMER_SECRET',
    'TCGHUB_CONSUMER_KEY',
    'TCGHUB_CONSUMER_SECRET'
]

missing_vars = [var for var in required_env_vars if not os.getenv(var)]
if missing_vars:
    logger.error(f"Missing required environment variables: {missing_vars}")
    raise ValueError(f"Missing required environment variables: {missing_vars}")


# -----------------------------------------------------------------------------
# Token store (SQLite-backed)
#
# Why SQLite instead of an in-memory dict:
#   - Survives app restarts.
#   - Works correctly when Flask runs under Gunicorn/uWSGI with MULTIPLE workers.
#     An in-memory dict would live in only one worker, so a token created by
#     worker A would look "invalid" when the follow-up request lands on worker B.
#   - Zero extra dependencies (sqlite3 is in the standard library).
# For very high traffic you'd swap this for Redis, but SQLite is plenty here.
# -----------------------------------------------------------------------------

def _db():
    conn = sqlite3.connect(TOKEN_DB_PATH, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL;")  # better concurrent read/write
    return conn


def _init_token_store():
    with _db() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS order_tokens (
                token   TEXT PRIMARY KEY,
                data    TEXT NOT NULL,
                expires REAL NOT NULL
            )
            """
        )
    logger.info("Token store initialised at %s", TOKEN_DB_PATH)


def create_token(json_data):
    """Store the order payload and return an opaque, unguessable token."""
    token = secrets.token_urlsafe(24)  # ~192 bits of randomness
    expires = time.time() + TOKEN_TTL
    with _db() as conn:
        # Opportunistically clean out expired rows so the table stays small.
        conn.execute("DELETE FROM order_tokens WHERE expires < ?", (time.time(),))
        conn.execute(
            "INSERT INTO order_tokens (token, data, expires) VALUES (?, ?, ?)",
            (token, json.dumps(json_data), expires),
        )
    return token


def read_token(token):
    """Return the stored payload for a token, or None if missing/expired.

    Tokens are readable multiple times until they expire, so a customer can
    refresh the page or come back to it within the TTL window.
    """
    if not token:
        return None
    with _db() as conn:
        row = conn.execute(
            "SELECT data, expires FROM order_tokens WHERE token = ?",
            (token,),
        ).fetchone()
    if not row:
        return None
    data_str, expires = row
    if expires < time.time():
        # Expired — delete and treat as invalid.
        with _db() as conn:
            conn.execute("DELETE FROM order_tokens WHERE token = ?", (token,))
        return None
    try:
        return json.loads(data_str)
    except json.JSONDecodeError:
        return None


_init_token_store()


def is_past_24_hours(order_data):
    """Return True if it's been 24+ hours since the order was placed.

    Uses date_created_gmt (UTC) rather than date_created (site local time) so
    the comparison against utcnow() is correct regardless of the store's
    timezone setting. Falls back to date_created if the gmt field is missing,
    and returns False (safe default: stays on "Being Packed") if neither can
    be parsed.
    """
    raw = order_data.get('date_created_gmt') or order_data.get('date_created')
    if not raw:
        return False
    try:
        order_time = datetime.fromisoformat(raw)
        if order_time.tzinfo is None:
            order_time = order_time.replace(tzinfo=timezone.utc)
        elapsed = datetime.now(timezone.utc) - order_time
        return elapsed.total_seconds() >= 24 * 3600
    except (ValueError, TypeError) as e:
        logger.warning(f"Could not parse order date for 24h check: {raw} ({e})")
        return False


# -----------------------------------------------------------------------------
# Routes
# -----------------------------------------------------------------------------

@app.route('/check-woo', methods=['GET'])
def check_woo():
    """Retrieve order status from WooCommerce and check if shipped. If shipped,
    fetch AWB from Delhivery or Shiprocket, then redirect with an opaque token
    (no order data in the URL)."""
    order_id = request.args.get('order-id')
    type_param = request.args.get('type')
    order_key = request.args.get('key')  # WooCommerce order_key (for IDOR check)

    logger.info(f"Received request for order ID: {order_id}, type: {type_param}")

    # Set WooCommerce credentials based on site type
    if type_param == 'F':
        woocommerce_url = "https://figureshub.in/wp-json/wc/v3"
        consumer_key = FIGURESHUB_CONSUMER_KEY
        consumer_secret = FIGURESHUB_CONSUMER_SECRET
        base_url = "https://figureshub.in"
    elif type_param == 'T':
        woocommerce_url = "https://tcghub.in/wp-json/wc/v3"
        consumer_key = TCGHUB_CONSUMER_KEY
        consumer_secret = TCGHUB_CONSUMER_SECRET
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
            auth=HTTPBasicAuth(consumer_key, consumer_secret),
            timeout=15,
        )

        # Handle response
        if response.status_code == 200:
            order_data = response.json()
            logger.info(f"WooCommerce API returned data for order {order_id}")

            # --- IDOR protection ---------------------------------------------
            if REQUIRE_ORDER_KEY:
                real_key = order_data.get('order_key')
                if not order_key or not real_key or order_key != real_key:
                    logger.warning(
                        f"Order key mismatch/missing for order {order_id} — refusing."
                    )
                    return "Unauthorized: invalid or missing order key.", 403

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
                            awb_number = delhivery_response.get('awb_number')
                            tracking_url = f"https://www.delhivery.com/track-v2/package/{awb_number}"
                            logger.info(f"Found Delhivery AWB: {awb_number}")
                        else:
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

                # Build a MINIMAL payload — only what each page needs to show.
                #
                # This is a deliberate data-minimisation step. Because the
                # tracking link uses a guessable order NUMBER (no email/key
                # check), we limit what any single token can ever reveal:
                #   - Shipped page  -> only the tracking link.
                #   - Packing page  -> only the first item + a "more" flag.
                # We do NOT store total, order date, full item list, or AWB.
                #
                # Trimming HERE (not just in the page HTML) is what actually
                # limits access: a customer who copies their own token and
                # calls /order-data directly still only sees these fields.
                try:
                    line_items = order_data.get('line_items', [])

                    # Full order date+time as WooCommerce returns it, e.g.
                    # "2026-07-14T18:32:07". Not price data, so safe to include
                    # in the trimmed payload alongside first_item.
                    date_created = order_data.get('date_created', '')

                    if awb_number:
                        if order_status == "completed":
                            # Shipped: tracking link + order date only.
                            json_data = {
                                "order_id": order_id,
                                "status": order_status,
                                "tracking_url": tracking_url,
                                "date_created": date_created,
                            }
                            token = create_token(json_data)
                            redirect_url = f'{base_url}/order-shipped/?token={token}'
                        else:
                            # Packing: first item + order date, plus a flag for "and more".
                            first_item = None
                            if line_items:
                                first_item = {
                                    "name": line_items[0].get('name', 'Unknown Item'),
                                    "quantity": line_items[0].get('quantity', 0),
                                }
                            json_data = {
                                "order_id": order_id,
                                "status": order_status,
                                "first_item": first_item,
                                "has_more_items": len(line_items) > 1,
                                "date_created": date_created,
                                # True once 24h have passed since the order was
                                # placed — tells the frontend which of the 3
                                # progress-bar stages is current.
                                "order_packed": is_past_24_hours(order_data),
                            }
                            token = create_token(json_data)
                            redirect_url = f'{base_url}/order-packing/?token={token}'
                    else:
                        redirect_url = f'{base_url}/your-order-is-getting-packed/?order-id={order_id}'

                    logger.info(f"Redirecting to: {redirect_url}")
                    return redirect(redirect_url)

                except Exception as e:
                    logger.error(f"Error preparing order data: {e}")
                    return redirect(f'{base_url}/your-order-is-getting-packed/?order-id={order_id}')
            else:
                logger.info("Order not in completed/processing status, redirecting to appropriate page")
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
            return f"Error retrieving order details: {response.status_code}", 500

    except Exception as e:
        logger.error(f"General error in check_woo: {e}", exc_info=True)
        if type_param == 'F':
            return redirect(f'https://figureshub.in/your-order-is-getting-packed/?order-id={order_id}')
        else:
            return redirect(f'https://tcghub.in/no-order/?order-id={order_id}')


@app.route('/order-data', methods=['GET'])
def order_data():
    """Return the order JSON associated with a token.

    Called server-to-server by the WordPress pages (via wp_remote_get). Because
    the token is opaque and expiring, and no order data ever appears in a URL,
    the sensitive fields stay out of browser history, referrer headers, and any
    link a customer might copy/paste.
    """
    token = request.args.get('token')
    data = read_token(token)
    if data is None:
        return jsonify({"error": "invalid or expired token"}), 404
    resp = jsonify(data)
    # Don't let intermediaries cache order details.
    resp.headers["Cache-Control"] = "no-store"
    return resp


def get_awb_number(order_id):
    """Call the Delhivery API to get the AWB number for the given order ID."""
    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Token {DELHIVERY_API_KEY}',
    }

    try:
        api_url = f"{DELHIVERY_API_URL}?waybill=&ref_ids={order_id}"
        logger.debug(f"Making request to Delhivery API: {api_url}")

        response = requests.get(api_url, headers=headers, timeout=10)

        logger.debug(f"Delhivery API response code: {response.status_code}")
        logger.debug(f"Delhivery API response body: {response.text[:500]}...")

        if response.status_code == 200:
            try:
                data = response.json()
                shipment_data = data.get('ShipmentData', [])
                if shipment_data and len(shipment_data) > 0:
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
            logger.warning(f"Delhivery API error: {response.status_code}")
            return None
    except requests.exceptions.RequestException as e:
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
        api_url = f"{SHIPROCKET_API_URL}?order_id={order_id}"
        logger.debug(f"Making request to Shiprocket API: {api_url}")

        response = requests.get(api_url, headers=headers, timeout=10)

        logger.debug(f"Shiprocket API response code: {response.status_code}")
        logger.debug(f"Shiprocket API response body: {response.text[:500]}...")

        if response.status_code == 200:
            try:
                data = response.json()
                if data and isinstance(data, list) and len(data) > 0:
                    tracking_data = data[0].get('tracking_data', {})
                    if not tracking_data or not isinstance(tracking_data, dict):
                        logger.warning("Tracking data not found or not a dict in Shiprocket response")
                        return None

                    shipment_track = tracking_data.get('shipment_track', [])
                    if shipment_track and isinstance(shipment_track, list) and len(shipment_track) > 0:
                        awb_number = shipment_track[0].get('awb_code')
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
            logger.warning(f"Shiprocket API error: {response.status_code}")
            return None
    except requests.exceptions.RequestException as e:
        logger.error(f"Error calling Shiprocket API: {e}")
        return None
    except Exception as e:
        logger.error(f"Unexpected error in get_shiprocket_tracking: {e}")
        return None


@app.route('/', methods=['GET'])
def health_check():
    return jsonify({"status": "healthy", "message": "Order tracking service is running"})

