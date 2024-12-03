from flask import Flask, request, redirect, abort
import sqlite3
import boto3
from botocore.exceptions import NoCredentialsError

app = Flask(__name__)

# Initialize database settings
DATABASE = 'orders.db'

# Initialize AWS S3 settings (only S3_BUCKET_NAME is required)
S3_BUCKET_NAME = 'thehuborders'  # Replace with your S3 bucket name

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
    """Upload a file to AWS S3."""
    if 'file' not in request.files:
        return "No file part", 400

    file = request.files['file']
    if file.filename == '':
        return "No selected file", 400

    try:
        # Upload the file to S3
        s3.upload_fileobj(file, S3_BUCKET_NAME, file.filename)
        return f"File {file.filename} uploaded successfully to S3!", 200
    except NoCredentialsError:
        return "Credentials not available", 403
    except Exception as e:
        return f"Error uploading file: {e}", 500


if __name__ == '__main__':
    init_db()
    app.run(debug=True, host='0.0.0.0')
