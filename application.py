from flask import Flask, request, redirect, jsonify
import sqlite3
import boto3
from botocore.exceptions import NoCredentialsError
import requests
from requests.auth import HTTPBasicAuth
import json
import urllib.parse

app = Flask(__name__)

# Initialize database settings
DATABASE = 'orders.db'

# Initialize AWS S3 settings (only S3_BUCKET_NAME is required)
S3_BUCKET_NAME = 'thehuborders'  # Replace with your S3 bucket name

# WooCommerce API credentials
WOOCOMMERCE_URL = "https://figureshub.in/wp-json/wc/v3"
CONSUMER_KEY = "ck_adf3760d0edad5ed2878b3098259457b14da15f1"
CONSUMER_SECRET = "cs_be0f2a6b00625d5a90e770711aa7aef8823de913"
# AWS S3 client (no need to manually provide keys if running on EC2 with an IAM role)
s3 = boto3.client('s3')  # IAM role credentials will be automatically used


def init_db():
    """Initialize the database with a sample orders table."""
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id TEXT UNIQUE NOT NULL
        )
    ''')
    # Add some sample data if table is empty
    cursor.execute("SELECT COUNT(*) FROM orders")
    if cursor.fetchone()[0] == 0:
        sample_data = [('12345',), ('67890',)]
        cursor.executemany("INSERT INTO orders (order_id) VALUES (?)", sample_data)
    conn.commit()
    conn.close()


@app.route('/check-order', methods=['GET'])
def check_order():
    """Check if order-id exists in the database and redirect."""
    order_id = request.args.get('order-id')
    if not order_id:
        return "Missing 'order-id' parameter!", 400

    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM orders WHERE order_id = ?", (order_id,))
    order = cursor.fetchone()
    conn.close()

    if order:
        # Redirect to the success page with the order ID in the URL
        return redirect(f'https://figureshub.in/order-shipped-3/?order-id={order_id}')
    else:
        # Redirect to the failure page with the order ID in the URL
        return redirect(f'https://figureshub.in/your-order-is-getting-packed/?order-id={order_id}')


@app.route('/upload-to-s3', methods=['POST'])
def upload_to_s3():
    """Upload order number to the database and S3."""
    order_no = request.args.get('order-no')
    if not order_no:
        return "Missing 'order-no' parameter!", 400

    try:
        # Insert the order number into the database
        conn = sqlite3.connect(DATABASE)
        cursor = conn.cursor()
        cursor.execute("INSERT INTO orders (order_id) VALUES (?)", (order_no,))
        conn.commit()
        conn.close()

        # Upload a confirmation file to S3
        content = f"Order Number: {order_no}"
        file_key = f"orders/{order_no}.txt"  # Store files in an "orders" folder in S3
        s3.put_object(Body=content, Bucket=S3_BUCKET_NAME, Key=file_key)

        return f"Order number {order_no} added to the database and uploaded to S3!", 200
    except sqlite3.IntegrityError:
        return f"Order number {order_no} already exists in the database.", 400
    except NoCredentialsError:
        return "Credentials not available", 403
    except Exception as e:
        return f"Error processing order number: {e}", 500


@app.route('/check-woo', methods=['GET'])
def check_woo():
    """Retrieve order status from WooCommerce and redirect with JSON data."""
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
            json_data = {
                "order_id": order_id,
                "status": order_data.get('status', 'Unknown'),
                "total": order_data.get('total', 'Unknown'),
                "date_created": order_data.get('date_created', 'Unknown')
            }
            # Use json.dumps() to ensure valid JSON and then URL-encode
            encoded_json = urllib.parse.quote(json.dumps(json_data))
            return redirect(f'https://figureshub.in/order-shipped/?order-data={encoded_json}')
        elif response.status_code == 404:
            return redirect(f'https://figureshub.in/your-order-is-getting-packed/?order-id={order_id}')
        else:
            return f"Error retrieving order details: {response.status_code}, {response.text}", 500
    except Exception as e:
        return f"An error occurred: {e}", 500


if __name__ == '__main__':
    init_db()
    app.run(debug=True, host='0.0.0.0')
