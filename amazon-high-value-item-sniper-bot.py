import os
import json
import time
import requests
from typing import Dict, Any, Optional
from pathlib import Path
from dotenv import load_dotenv

class AmazonStockChecker:
    """
    Basic Amazon product stock monitoring class.
    
    Initializes the foundation for monitoring a product's stock on Amazon.
    
    Parameters:
        product_url (str): The URL of the Amazon product to monitor
        email (str): Amazon account email for login
        password (str): Amazon account password for login
        max_price (float): Maximum price to consider for purchase
    """
    def __init__(self, product_url: str, email: str, password: str, max_price: float = 2800.0) -> None:
        self.product_url = product_url
        self.email = email
        self.password = password
        self.max_price = float(max_price)
        self.session = requests.Session()
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/96.0.4664.110 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml',
            'Accept-Language': 'en-US,en;q=0.9',
            'Connection': 'keep-alive',
            'Cache-Control': 'max-age=0'
        }
        
    def load_purchase_record(self) -> None:
        """
        Load the purchase history from a JSON file.
        
        Creates an empty record if the file doesn't exist.
        """
        try:
            if os.path.exists(self.purchase_record_file):
                with open(self.purchase_record_file, 'r') as f:
                    self.purchase_record = json.load(f)
            else:
                self.purchase_record = {}
                with open(self.purchase_record_file, 'w') as f:
                    json.dump(self.purchase_record, f)
        except Exception:
            self.purchase_record = {}

def create_env_file() -> bool:
    """
    Create a .env file with user input if it doesn't exist.
    
    Returns:
        bool: True if the .env file exists after function execution
    """
    env_path = Path('.env')
    
    if not env_path.exists():
        email = input("Enter your Amazon email: ")
        password = input("Enter your Amazon password: ")
        max_price = input("Enter maximum price (default: $2800.00): ") or "2800.00"
        product_url = input("Enter Amazon product URL: ")
        
        with open(env_path, 'w') as f:
            f.write(f"AMAZON_EMAIL={email}\n")
            f.write(f"AMAZON_PASSWORD={password}\n")
            f.write(f"MAX_PRICE={max_price}\n")
            f.write(f"PRODUCT_URL={product_url}\n")
    
    return env_path.exists()

if __name__ == "__main__":
    if not create_env_file():
        print("Error: Could not create .env file")
        exit(1)
    
    load_dotenv()
    
    email = os.getenv("AMAZON_EMAIL")
    password = os.getenv("AMAZON_PASSWORD")
    max_price = os.getenv("MAX_PRICE")
    product_url = os.getenv("PRODUCT_URL")
    
    if not email or not password or not product_url:
        print("Error: Required environment variables missing from .env file")
        exit(1)
    
    print("Environment loaded successfully")