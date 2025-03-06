import os
import json
import time
import random
import sys
import re
import threading
import requests
from typing import Dict, Any, Optional, Tuple, Union, List
from pathlib import Path
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from dotenv import load_dotenv

class SuppressOutput:
    """
    Context manager to temporarily suppress stdout and stderr output.
    
    Used to prevent verbose messages from ChromeDriverManager and Selenium.
    """
    def __enter__(self) -> None:
        """
        Redirect stdout and stderr to null device on context entry.
        """
        self._original_stdout = sys.stdout
        self._original_stderr = sys.stderr
        sys.stdout = open(os.devnull, 'w')
        sys.stderr = open(os.devnull, 'w')

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """
        Restore original stdout and stderr on context exit.
        
        Parameters:
            exc_type: Exception type if an exception occurred
            exc_val: Exception value if an exception occurred
            exc_tb: Exception traceback if an exception occurred
        """
        sys.stdout.close()
        sys.stderr.close()
        sys.stdout = self._original_stdout
        sys.stderr = self._original_stderr

class AmazonStockChecker:
    """
    Amazon product stock monitoring class with Selenium integration.
    
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
        self.purchase_record_file = 'purchase_record.json'
        self.load_purchase_record()
        self.purchase_attempted = False
        self.purchase_successful = False
        self.driver = None
        self.session = requests.Session()
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/96.0.4664.110 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml',
            'Accept-Language': 'en-US,en;q=0.9',
            'Connection': 'keep-alive',
            'Cache-Control': 'max-age=0'
        }
        
        # Initialize browser
        self.initialize_browser()
        
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
            
    def has_been_purchased(self) -> bool:
        """
        Check if the current product has already been purchased.
        
        Returns:
            bool: True if the product URL exists in the purchase record
        """
        return self.product_url in self.purchase_record
    
    def mark_as_purchased(self) -> None:
        """
        Mark the current product as purchased in the record file.
        
        Updates the purchase record with timestamp and date information.
        """
        try:
            self.purchase_record[self.product_url] = {
                'purchased_at': time.time(),
                'date': time.strftime('%Y-%m-%d %H:%M:%S')
            }
            with open(self.purchase_record_file, 'w') as f:
                json.dump(self.purchase_record, f)
            self.purchase_successful = True
        except Exception:
            pass
            
    def preload_checkout_paths(self) -> None:
        """
        Preload checkout pages to improve checkout speed.
        
        Caches cart and checkout pages and prepares JavaScript for one-click purchasing.
        """
        print("Pre-loading checkout paths to improve speed...")
        try:
            # Preload cart page to cache it
            self.driver.get("https://www.amazon.com/gp/cart/view.html")
            
            # Preload checkout page
            self.driver.get("https://www.amazon.com/gp/checkout/select")
            
            # Back to product page
            self.driver.get(self.product_url)
            
            # Prepare one-click script (to bypass regular checkout if possible)
            self.one_click_js = """
            // Try to find and click one-click buy button if available
            const buyNowBtn = document.getElementById('buy-now-button');
            if (buyNowBtn) {
                buyNowBtn.click();
                return true;
            }
            
            // If one-click isn't available, try add to cart
            const addToCartBtn = document.getElementById('add-to-cart-button');
            if (addToCartBtn) {
                addToCartBtn.click();
                return true;
            }
            
            return false;
            """
            
            print("Checkout paths cached for speed")
        except Exception:
            print("Failed to pre-load checkout paths. Will continue anyway.")
            self.driver.get(self.product_url)
            
    def js_purchase_strategy(self) -> None:
        """
        Purchase strategy using direct JavaScript execution.
        
        This is typically the fastest method for quick checkout.
        """
        try:
            # Step 1: Buy directly using JavaScript (fastest method)
            self.driver.get(self.product_url)
            
            # Execute Buy JS
            added = self.driver.execute_script(self.one_click_js)
            if not added:
                return
            
            # If we reach here, proceed to checkout
            # Look for Place Order button on any page we end up on
            try:
                place_order_button = WebDriverWait(self.driver, 5).until(
                    EC.element_to_be_clickable((By.ID, "placeYourOrder"))
                )
                self.driver.execute_script("arguments[0].click();", place_order_button)
                self.mark_as_purchased()
            except:
                # Try to go to checkout directly if we can't find the button
                try:
                    self.driver.get("https://www.amazon.com/gp/checkout/select")
                    place_order_button = WebDriverWait(self.driver, 5).until(
                        EC.element_to_be_clickable((By.ID, "placeYourOrder"))
                    )
                    self.driver.execute_script("arguments[0].click();", place_order_button)
                    self.mark_as_purchased()
                except:
                    pass
        except:
            pass
    
    def buy_now_strategy(self) -> None:
        """
        Purchase strategy using the Buy Now button.
        
        Alternative path if JavaScript execution fails.
        """
        try:
            # Get fresh page
            self.driver.get(self.product_url)
            
            # Click Buy Now button
            buy_now = WebDriverWait(self.driver, 2).until(
                EC.element_to_be_clickable((By.ID, "buy-now-button"))
            )
            self.driver.execute_script("arguments[0].click();", buy_now)
            
            # Try to place order
            try:
                place_order_button = WebDriverWait(self.driver, 5).until(
                    EC.element_to_be_clickable((By.ID, "placeYourOrder"))
                )
                self.driver.execute_script("arguments[0].click();", place_order_button)
                self.mark_as_purchased()
            except:
                pass
        except:
            pass
    
    def cart_strategy(self) -> None:
        """
        Purchase strategy using Add to Cart + Express Checkout.
        
        Fallback strategy if other methods fail.
        """
        try:
            # Get fresh page
            self.driver.get(self.product_url)
            
            # Add to cart
            add_to_cart_button = WebDriverWait(self.driver, 2).until(
                EC.element_to_be_clickable((By.ID, "add-to-cart-button"))
            )
            self.driver.execute_script("arguments[0].click();", add_to_cart_button)
            
            # Try to skip cart and go straight to checkout
            try:
                # First try to find the proceed to checkout button in the add-to-cart success popup
                proceed_button = WebDriverWait(self.driver, 2).until(
                    EC.element_to_be_clickable((By.ID, "hlb-ptc-btn-native"))
                )
                self.driver.execute_script("arguments[0].click();", proceed_button)
            except:
                # If that fails, try to go to cart page
                try:
                    self.driver.get("https://www.amazon.com/gp/cart/view.html")
                    proceed_button = WebDriverWait(self.driver, 2).until(
                        EC.element_to_be_clickable((By.NAME, "proceedToRetailCheckout"))
                    )
                    self.driver.execute_script("arguments[0].click();", proceed_button)
                except:
                    # If that fails, try to access checkout directly
                    self.driver.get("https://www.amazon.com/gp/checkout/select")
            
            # Look for the place order button
            try:
                place_order_button = WebDriverWait(self.driver, 5).until(
                    EC.element_to_be_clickable((By.ID, "placeYourOrder"))
                )
                self.driver.execute_script("arguments[0].click();", place_order_button)
                self.mark_as_purchased()
            except:
                pass
        except:
            pass
    
    def ultra_fast_purchase(self) -> bool:
        """
        Optimized fast checkout using parallel strategies.
        
        Runs three checkout strategies simultaneously for the fastest possible
        purchase when stock is detected.
        
        Returns:
            bool: True if purchase was successful
        """
        if self.has_been_purchased() or self.purchase_attempted:
            return False
            
        self.purchase_attempted = True
        print("\n🚨 INITIATING LIGHTNING FAST CHECKOUT! 🚨")
        
        try:
            # EXECUTE CRITICAL PATH: THREE PARALLEL STRATEGIES
            
            # Strategy 1: Direct JavaScript execution (fastest)
            js_thread = threading.Thread(target=self.js_purchase_strategy, daemon=True)
            
            # Strategy 2: Buy Now button
            buy_now_thread = threading.Thread(target=self.buy_now_strategy, daemon=True)
            
            # Strategy 3: Add to cart + express checkout
            cart_thread = threading.Thread(target=self.cart_strategy, daemon=True)
            
            # Start all strategies simultaneously
            js_thread.start()
            buy_now_thread.start()
            cart_thread.start()
            
            # Wait up to 15 seconds for any strategy to complete
            max_wait = 15
            start = time.time()
            while time.time() - start < max_wait:
                if self.purchase_successful:
                    print("🔥 ORDER SUCCESSFULLY PLACED! 🔥")
                    return True
                time.sleep(0.1)
            
            # If no success but we're in checkout, keep browser open for manual completion
            if "checkout" in self.driver.current_url.lower():
                print("Automated checkout in progress but not completed.")
                print("Browser window open for manual completion")
            else:
                self.purchase_attempted = False
                print("Fast checkout failed. Will retry on next stock detection.")
            
            return self.purchase_successful
            
        except Exception as e:
            self.purchase_attempted = False
            print(f"Error during purchase: {str(e)}")
            return False
    
    def initialize_browser(self) -> None:
        """
        Initialize Chrome browser with optimized settings for automation.
        
        Sets up a headless Chrome browser with various performance optimizations
        and initiates the login process.
        """
        with SuppressOutput():
            chrome_options = Options()
            
            chrome_args = (
                "--disable-gpu",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-extensions",
                "--disable-logging",
                "--log-level=3",
                "--disable-infobars",
                "--disable-notifications",
                "--disable-default-apps",
                "--disable-background-networking",
                "--disable-background-timer-throttling",
                "--disable-client-side-phishing-detection",
                "--disable-hang-monitor",
                "--disable-popup-blocking",
                "--disable-prompt-on-repost",
                "--disable-sync",
                "--metrics-recording-only",
                "--no-first-run",
                "--safebrowsing-disable-auto-update",
                "--js-flags=--expose-gc",
                "--disable-features=TranslateUI",
                "--disable-translate",
                "--dns-prefetch-disable"
            )
            
            for arg in chrome_args:
                chrome_options.add_argument(arg)
            
            chrome_prefs = {
                "profile.default_content_setting_values.notifications": 2,
                "profile.managed_default_content_settings.images": 1,
                "disk-cache-size": 4096,
                "safebrowsing.enabled": False
            }
            chrome_options.add_experimental_option("prefs", chrome_prefs)
            
            chrome_options.add_experimental_option('excludeSwitches', ('enable-logging', 'enable-automation'))
            chrome_options.add_experimental_option('useAutomationExtension', False)
            
            service = Service(ChromeDriverManager().install())
            self.driver = webdriver.Chrome(service=service, options=chrome_options)
            
            self.driver.set_page_load_timeout(15)
            self.driver.implicitly_wait(0.5)
            
            print("Browser initialized. Logging in to Amazon...")
            self.login()
    
    def login(self) -> None:
        """
        Log in to Amazon using the provided credentials.
        
        Handles the login flow including potential 2FA verification.
        """
        try:
            self.driver.get("https://www.amazon.com/ap/signin?openid.pape.max_auth_age=0&openid.return_to=https%3A%2F%2Fwww.amazon.com%2F%3Fref_%3Dnav_signin&openid.identity=http%3A%2F%2Fspecs.openid.net%2Fauth%2F2.0%2Fidentifier_select&openid.assoc_handle=usflex&openid.mode=checkid_setup&openid.claimed_id=http%3A%2F%2Fspecs.openid.net%2Fauth%2F2.0%2Fidentifier_select&openid.ns=http%3A%2F%2Fspecs.openid.net%2Fauth%2F2.0")
            
            # Enter email with a slight delay between characters
            email_field = WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.ID, "ap_email"))
            )
            email_field.clear()
            for character in self.email:
                email_field.send_keys(character)
                time.sleep(random.uniform(0.05, 0.15))
            
            continue_button = WebDriverWait(self.driver, 5).until(
                EC.element_to_be_clickable((By.ID, "continue"))
            )
            time.sleep(0.5)  # Short pause before clicking
            continue_button.click()
            
            # Enter password with a slight delay between characters
            password_field = WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.ID, "ap_password"))
            )
            password_field.clear()

            for character in self.password:
                password_field.send_keys(character)
                time.sleep(random.uniform(0.05, 0.15))
            
            time.sleep(0.5)  # Short pause before clicking
            sign_in_button = WebDriverWait(self.driver, 5).until(
                EC.element_to_be_clickable((By.ID, "signInSubmit"))
            )
            sign_in_button.click()
            
            # Check for possible 2FA requirement
            try:
                WebDriverWait(self.driver, 3).until(
                    EC.presence_of_element_located((By.ID, "auth-mfa-otpcode"))
                )
                print("\n*** 2FA REQUIRED ***")
                print("Enter the code sent to your device in the browser window")
                print("The bot will continue automatically after you complete 2FA\n")
                
                WebDriverWait(self.driver, 60).until(
                    EC.presence_of_element_located((By.ID, "twotabsearchtextbox"))
                )
            except TimeoutException:
                pass
            
            # Verify login success
            try:
                WebDriverWait(self.driver, 5).until(
                    EC.presence_of_element_located((By.ID, "nav-link-accountList"))
                )
                print("Successfully logged in to Amazon")
            except TimeoutException:
                print("Login might have failed or requires additional verification")
                print("Please check the browser window and complete any steps manually")
                print("The bot will continue once you're logged in")
                
                WebDriverWait(self.driver, 120).until(
                    EC.presence_of_element_located((By.ID, "twotabsearchtextbox"))
                )
            
            # Pre-load the product page to warm up the cache
            self.driver.get(self.product_url)
            print("Ready to monitor product for stock")
            
        except Exception as e:
            print(f"Error during login: {str(e)}")
            print("Please complete login manually in the browser window")
            
            input("Press Enter after completing login manually...")

    

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