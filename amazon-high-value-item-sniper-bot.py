import os
import json
import time
import random
import threading
import requests
import re
import sys
import signal
import atexit
from pathlib import Path
from typing import Dict, Any, Optional, Union, Tuple, List, Callable
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from dotenv import load_dotenv

# Global variables for cleanup tracking
exit_requested = False
exit_in_progress = False

class SuppressOutput:
    """Context manager to temporarily suppress stdout and stderr output."""
    
    def __enter__(self) -> 'SuppressOutput':
        self._original_stdout = sys.stdout
        self._original_stderr = sys.stderr
        sys.stdout = open(os.devnull, 'w')
        sys.stderr = open(os.devnull, 'w')
        return self

    def __exit__(self, exc_type: Optional[type], exc_val: Optional[Exception], exc_tb: Optional[Any]) -> None:
        sys.stdout.close()
        sys.stderr.close()
        sys.stdout = self._original_stdout
        sys.stderr = self._original_stderr

class AmazonUltraFastBot:
    """
    Ultra-fast bot for monitoring Amazon product availability and automatic purchase.
    
    This bot continuously monitors a specified Amazon product URL for stock availability
    and automatically attempts to purchase the item when it becomes available at or below
    the specified maximum price.
    """
    
    def __init__(self, product_url: str, email: str, password: str, max_price: float, check_interval: float = 0.05) -> None:
        """
        Initialize the Amazon Ultra Fast Bot.
        
        Args:
            product_url: URL of the Amazon product to monitor
            email: Amazon account email
            password: Amazon account password
            max_price: Maximum price to trigger purchase
            check_interval: Time between stock checks in seconds (default: 0.05)
        """
        self.product_url = product_url
        self.email = email
        self.password = password
        self.max_price = float(max_price)
        self.check_interval = check_interval
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
        self.last_status_time = time.time()
        self.check_count = 0
        self.exit_requested = False
        
        # Set up signal handlers for graceful shutdown
        signal.signal(signal.SIGINT, self.signal_handler)
        signal.signal(signal.SIGTERM, self.signal_handler)
        
        # Register at-exit handler as a fallback
        atexit.register(self.cleanup)
        
        # Initialize browser
        self.initialize_browser()
    
    def signal_handler(self, sig: int, frame: Any) -> None:
        """
        Handle interrupt signals with double-press detection for forced exit.
        
        Args:
            sig: Signal number
            frame: Current stack frame
        """
        global exit_in_progress, exit_requested
        
        if exit_in_progress:
            return
            
        current_time = time.time()
        exit_in_progress = True
        
        if hasattr(self, 'last_ctrl_c_time'):
            if current_time - self.last_ctrl_c_time < 0.5:
                exit_in_progress = False
                return
                
            if current_time - self.last_ctrl_c_time < 2:
                print("\nForce closing browser and exiting...")
                try:
                    if self.driver:
                        try:
                            _ = self.driver.current_url
                            self.driver.quit()
                            print("Browser closed successfully.")
                        except Exception:
                            print("Browser appears to be already closed.")
                except Exception as e:
                    print(f"Error while closing browser: {e}")
                finally:
                    print("Forcing program termination.")
                    exit_requested = True
                    os._exit(0)
        
        print("\nPress Ctrl+C again within 2 seconds to force exit")
        time.sleep(0.2)
        self.last_ctrl_c_time = time.time()
        
        try:
            self.cleanup()
        except Exception:
            pass
        
        exit_requested = True
        exit_in_progress = False
        
        sys.exit(0)
            
    def cleanup(self) -> None:
        """Safely clean up resources, checking if browser is already closed."""
        self.exit_requested = True
        if hasattr(self, 'driver') and self.driver:
            try:
                try:
                    _ = self.driver.current_url
                    print("Closing browser...")
                    self.driver.quit()
                    print("Browser closed successfully.")
                except Exception:
                    print("Browser appears to be already closed.")
            except Exception as e:
                print(f"Note: {e}")
        
    def load_purchase_record(self) -> None:
        """
        Load purchase history from file or create a new one if it doesn't exist.
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
            True if product has been purchased, False otherwise
        """
        return self.product_url in self.purchase_record
        
    def mark_as_purchased(self) -> None:
        """
        Mark the current product as purchased in the purchase record.
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
    
    def initialize_browser(self) -> None:
        """
        Initialize Chrome browser with optimized settings for fast automated checkout.
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
            "--dns-prefetch-disable",
            "--disable-web-security"
            "--disable-site-isolation-trials",
            "--ignore-certificate-errors",
            "--disable-setuid-sandbox",
            "--disable-accelerated-2d-canvas",
            "--disable-breakpad",
            "--disable-component-update",
            "--disable-domain-reliability",
            "--disable-features=site-per-process",
            "--disable-ipc-flooding-protection",
            "--enable-low-end-device-mode",
            "--disable-speech-api",
            "--memory-pressure-off",
            "--mute-audio",
            "--no-default-browser-check",
            "--no-pings",
            "--no-report-upload",
            "--no-zygote",
            "--single-process",
            "--disable-threaded-animation",
            "--disable-threaded-scrolling",
            "--headless=new",
            "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/96.0.4664.110 Safari/537.36"
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
            
            self.preload_checkout_paths()
    
    def preload_checkout_paths(self) -> None:
        """
        Pre-load checkout-related pages to improve purchase speed.
        """
        print("Pre-loading checkout paths to improve speed...")
        try:
            self.driver.get("https://www.amazon.com/gp/cart/view.html")
            self.driver.get("https://www.amazon.com/gp/checkout/select")
            self.driver.get(self.product_url)
            
            self.one_click_js = """
            // Try to trigger Buy Now first (highest priority)
            const buyNowBtn = document.getElementById('buy-now-button');
            if (buyNowBtn) {
                buyNowBtn.click();
                
                setTimeout(() => {
                    const placeOrder = document.getElementById('placeYourOrder');
                    if (placeOrder) placeOrder.click();
                }, 100);
                
                return true;
            }

            // Try Add to Cart as fallback
            const addToCartBtn = document.getElementById('add-to-cart-button');
            if (addToCartBtn) {
                addToCartBtn.click();
                
                setTimeout(() => {
                    const miniCartProceed = document.querySelector('#sw-ptc-form .a-button-input');
                    if (miniCartProceed) {
                        miniCartProceed.click();
                        return;
                    }
                    
                    const placeOrder = document.getElementById('placeYourOrder');
                    if (placeOrder) {
                        placeOrder.click();
                        return;
                    }
                    
                    const proceedCheckout = document.getElementById('sc-buy-box-ptc-button');
                    if (proceedCheckout) proceedCheckout.click();
                }, 100);
                
                return true;
            }

            return false;
            """
            
            print("Checkout paths cached for speed")
        except Exception:
            print("Failed to pre-load checkout paths. Will continue anyway.")
            self.driver.get(self.product_url)
    
    def login(self) -> None:
        """
        Log in to Amazon using the provided credentials.
        Handles 2FA if needed.
        """
        try:
            self.driver.get("https://www.amazon.com/ap/signin?openid.pape.max_auth_age=0&openid.return_to=https%3A%2F%2Fwww.amazon.com%2F%3Fref_%3Dnav_signin&openid.identity=http%3A%2F%2Fspecs.openid.net%2Fauth%2F2.0%2Fidentifier_select&openid.assoc_handle=usflex&openid.mode=checkid_setup&openid.claimed_id=http%3A%2F%2Fspecs.openid.net%2Fauth%2F2.0%2Fidentifier_select&openid.ns=http%3A%2F%2Fspecs.openid.net%2Fauth%2F2.0")
            
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
            time.sleep(0.5)
            continue_button.click()
            
            password_field = WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.ID, "ap_password"))
            )
            password_field.clear()

            for character in self.password:
                password_field.send_keys(character)
                time.sleep(random.uniform(0.05, 0.15))
            
            time.sleep(0.5)
            sign_in_button = WebDriverWait(self.driver, 5).until(
                EC.element_to_be_clickable((By.ID, "signInSubmit"))
            )
            sign_in_button.click()
            
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
            
            self.driver.get(self.product_url)
            print("Ready to monitor product for stock")
            
        except Exception as e:
            print(f"Error during login: {str(e)}")
            print("Please complete login manually in the browser window")
            
            input("Press Enter after completing login manually...")
    
    def extract_price(self, text: str) -> Optional[float]:
        """
        Extract price from text containing price information.
        
        Args:
            text: Text containing price information
            
        Returns:
            Extracted price as float or None if no price found
        """
        if not hasattr(self, 'price_patterns'):
            self.price_patterns = (
                re.compile(r'\$([0-9,]+\.[0-9]{2})'),
                re.compile(r'\$([0-9,]+)'),
                re.compile(r'([0-9,]+\.[0-9]{2})\s*\$'),
                re.compile(r'([0-9,]+\.[0-9]{2})')
            )
        
        for pattern in self.price_patterns:
            matches = pattern.findall(text)
            if matches:
                return float(matches[0].replace(',', ''))
        return None
    
    def get_product_price(self) -> Optional[float]:
        """
        Get the current price of the product.
        
        Returns:
            Current price as float or None if price couldn't be determined
        """
        try:
            try:
                price_js = self.driver.execute_script('''
                    const priceElements = [
                        document.querySelector("#priceblock_ourprice"),
                        document.querySelector("#priceblock_dealprice"),
                        document.querySelector(".a-price .a-offscreen"),
                        document.querySelector("#price_inside_buybox"),
                        document.querySelector(".a-section.a-spacing-none.aok-align-center .a-price .a-offscreen"),
                        document.querySelector(".priceToPay span.a-price-whole")
                    ];
                    
                    for (const el of priceElements) {
                        if (el && el.textContent) {
                            return el.textContent;
                        }
                    }
                    
                    const allElements = document.querySelectorAll("*");
                    for (const el of allElements) {
                        if (el.textContent && el.textContent.includes("$") && 
                            /\\$[0-9,]+(\.[0-9]{2})?/.test(el.textContent)) {
                            return el.textContent;
                        }
                    }
                    
                    return null;
                ''')
                
                if price_js:
                    extracted_price = self.extract_price(price_js)
                    if extracted_price:
                        return extracted_price
            except:
                pass
                
            price_selectors = (
                "#priceblock_ourprice", "#priceblock_dealprice", ".a-price .a-offscreen",
                "#price_inside_buybox", ".priceToPay span.a-price-whole"
            )
            
            for selector in price_selectors:
                try:
                    elements = self.driver.find_elements(By.CSS_SELECTOR, selector)
                    if elements:
                        for element in elements:
                            price_text = element.text or element.get_attribute("innerHTML")
                            if price_text:
                                extracted_price = self.extract_price(price_text)
                                if extracted_price:
                                    return extracted_price
                except:
                    continue
            
            return None
            
        except Exception:
            return None
    
    def check_stock_and_price(self) -> bool:
        """
        Check if the product is in stock and within the price limit.
        
        Returns:
            True if product is available and within price limit, False otherwise
        """
        try:
            try:
                is_available = self.driver.execute_script('''
                    const addToCartBtn = document.getElementById('add-to-cart-button');
                    if (addToCartBtn && !addToCartBtn.disabled) {
                        return true;
                    }
                    
                    const buyNowBtn = document.getElementById('buy-now-button');
                    if (buyNowBtn && !buyNowBtn.disabled) {
                        return true;
                    }
                    
                    const pageText = document.body.innerText;
                    if (pageText.includes('Currently unavailable')) {
                        return false;
                    }
                    
                    return false;
                ''')
                
                if is_available:
                    price = self.get_product_price()
                    if price is not None:
                        print(f"Current price: ${price:.2f}")
                        return price <= self.max_price
            except:
                pass
                
            try:
                with self.session.get(
                    self.product_url, 
                    headers=self.headers, 
                    timeout=0.5,
                    stream=True
                ) as response:
                    chunk = next(response.iter_content(chunk_size=5000))
                    content = chunk.decode('utf-8', errors='ignore')
                    
                    if "add-to-cart-button" in content and "Currently unavailable" not in content:
                        self.driver.get(self.product_url)
                        price = self.get_product_price()
                        if price is not None:
                            print(f"Current price: ${price:.2f}")
                            return price <= self.max_price
            except:
                pass
            
            return False
            
        except Exception:
            return False
    
    def ultra_fast_purchase(self) -> bool:
        """
        Execute ultra-fast purchase using multiple parallel strategies.
        
        Returns:
            True if purchase was successful, False otherwise
        """
        if self.has_been_purchased() or self.purchase_attempted:
            return False
            
        self.purchase_attempted = True
        print("\n🚨 INITIATING LIGHTNING FAST CHECKOUT! 🚨")
        
        try:
            js_thread = threading.Thread(target=self.js_purchase_strategy, daemon=True)
            buy_now_thread = threading.Thread(target=self.buy_now_strategy, daemon=True)
            cart_thread = threading.Thread(target=self.cart_strategy, daemon=True)
            
            js_thread.start()
            buy_now_thread.start()
            cart_thread.start()
            
            max_wait = 15
            start = time.time()
            while time.time() - start < max_wait:
                if self.purchase_successful:
                    print("🔥 ORDER SUCCESSFULLY PLACED! 🔥")
                    return True
                time.sleep(0.1)
            
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
    
    def js_purchase_strategy(self) -> None:
        """
        Purchase strategy using direct JavaScript execution.
        """
        try:
            self.driver.get(self.product_url)
            
            added = self.driver.execute_script(self.one_click_js)
            if not added:
                return
            
            try:
                place_order_button = WebDriverWait(self.driver, 5).until(
                    EC.element_to_be_clickable((By.ID, "placeYourOrder"))
                )
                self.driver.execute_script("arguments[0].click();", place_order_button)
                self.mark_as_purchased()
            except:
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
        Purchase strategy using Buy Now button.
        """
        try:
            self.driver.get(self.product_url)
            
            buy_now = WebDriverWait(self.driver, 2).until(
                EC.element_to_be_clickable((By.ID, "buy-now-button"))
            )
            self.driver.execute_script("arguments[0].click();", buy_now)
            
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
        """
        try:
            self.driver.get(self.product_url)
            
            add_to_cart_button = WebDriverWait(self.driver, 2).until(
                EC.element_to_be_clickable((By.ID, "add-to-cart-button"))
            )
            self.driver.execute_script("arguments[0].click();", add_to_cart_button)
            
            try:
                proceed_button = WebDriverWait(self.driver, 2).until(
                    EC.element_to_be_clickable((By.ID, "hlb-ptc-btn-native"))
                )
                self.driver.execute_script("arguments[0].click();", proceed_button)
            except:
                try:
                    self.driver.get("https://www.amazon.com/gp/cart/view.html")
                    proceed_button = WebDriverWait(self.driver, 2).until(
                        EC.element_to_be_clickable((By.NAME, "proceedToRetailCheckout"))
                    )
                    self.driver.execute_script("arguments[0].click();", proceed_button)
                except:
                    self.driver.get("https://www.amazon.com/gp/checkout/select")
            
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
    
    def refresh_browser_periodically(self) -> bool:
        """
        Periodically refresh the browser to prevent session timeouts.
        
        Returns:
            True if refresh was successful, False otherwise
        """
        try:
            current_url = self.driver.current_url
            if "amazon.com" in current_url and "/checkout/" not in current_url and not self.purchase_attempted:
                self.driver.refresh()
                return True
        except:
            try:
                self.driver.get(self.product_url)
                return True
            except:
                return False
        return False
                
    def monitor(self) -> None:
        """
        Main monitoring loop that continuously checks product availability.
        """
        global exit_requested
        print(f"Starting lightning-fast monitoring for: {self.product_url}")
        print(f"Maximum price set to ${self.max_price:.2f}")
        
        if self.has_been_purchased():
            print("This product has already been purchased. Monitoring canceled.")
            self.cleanup()
            return
        
        start_time = time.time()
        last_browser_refresh = time.time()
        browser_refresh_interval = 900  # Refresh browser every 15 minutes
        
        try:
            while not self.purchase_successful and not exit_requested:
                self.check_count += 1
                
                current_time = time.time()
                if self.check_count % 5000 == 0:
                    elapsed = current_time - start_time
                    checks_per_second = self.check_count / elapsed
                    print(f"Status: {self.check_count} checks performed. Time elapsed: {elapsed:.2f} seconds. Rate: {checks_per_second:.2f} checks/second")
                    self.last_status_time = current_time
                
                if current_time - last_browser_refresh > browser_refresh_interval:
                    self.refresh_browser_periodically()
                    last_browser_refresh = current_time
                
                if self.check_stock_and_price():
                    print("\n🚨 PRODUCT IN STOCK AND UNDER PRICE LIMIT! 🚨")
                    
                    if self.ultra_fast_purchase():
                        print("Purchase successful! Monitoring stopped.")
                        self.cleanup()
                        return
                    else:
                        print("Continuing to monitor for another attempt...")
                
                time.sleep(random.uniform(0.005, self.check_interval))
                
        except KeyboardInterrupt:
            print("\nMonitoring stopped by user.")
        except Exception as e:
            print(f"Error occurred: {str(e)}. Restarting monitoring...")
            time.sleep(5)
            if not exit_requested:
                self.monitor()
        finally:
            self.cleanup()


def create_env_file() -> bool:
    """
    Create .env file with user input if it doesn't exist.
    
    Returns:
        True if .env file exists or was created successfully, False otherwise
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

def print_animated_logo() -> Tuple[int, str]:
    """
    Prints the logo with a simple typing animation effect and loading bar.
    
    Returns:
        Tuple containing the length of the last line of the logo and the program name
    """
    def clear_screen() -> None:
        if os.name == 'nt':
            os.system('cls')
        else:
            os.system('clear')

    program_name = "Roka's Unary Light-Speed Amazon Stock Checker & Auto-Buyer Snipper"

    logo_lines = (
            "                      ███                                                                  █████    ",
            "                    ██▓▓████                                                       ██████▓▓▒▒░▒██   ",
            "                   ██▒▒▓▒░▒███                                                 ███▓▓██▓▒░░░▒▓▓▒██   ",
            "                  █▓▒▒▒▓▓▒░░▒███                                             ██▓▒▒░░░░░░▒▓▒▒▓▒▒██   ",
            "                 ██▒▒▒▒▓▒▒░░░░▒███                                        ██▓▒▒▒░░░░░▒▓▓▒▒▒▒▓▒▒██   ",
            "                 █▓▒▒▓▓▒▒▓▒░░░░░▒██                                     ███▒▒▒░░░░░▒▓▓▒▒▒▒▒▒▓▒▒███  ",
            "                ██▒▒▒▓▒▒▒▒▓▒░░░░░░▓██                                  ██▒▒▒░░░░░▒▓▒▒▓▓▒▒▒▒▒▓▒▒███  ",
            "                ██▒▒▓▓▒▒▒▒▒▓▒░░░░░░▒██                    ██████████ ██▓▒▒▒▒▒░░▒▓▓▒▓▓▒▒▒▒▒▒▒▒░▒███  ",
            "                █▓▒▒▓▒▒▒▒▒▒▒▓▒░░░░░░░▓██                ██▓░░░░░░▒▒▓███▓▒▒▒▒▒░░▒░▒▓▒▒▓▒▒▒▒▒▒▒░▒██   ",
            "               ██▓▒▒▓▒▒▒▒▒▒▒▒▓▓░░░░░░▒▓██               ██▒▒▒▒▒▓████▓▒▒▒▒▒▒▒░░░░▓▓▓▓▒▒▓▒▒▒▒▒▒░▒██   ",
            "               ██▒▒▓▒▒▒▒▒▒▒▒▓▓▒▓▒▒▒▒▒▒▒▒██▓███████████████▒▒▒▓█████▒▒▒▒▒▒▒▒░░░▒▓▓▒░░░▓▓▒▒▒▒▒░░▓██   ",
            "               ██▓▒▓▒▒▒▒▒▒▒▓▓▓▒▒▓▓▒▒▒▒▒▒▒▒▒▓████▓▓▒▒▒▒▒▓▓▓▓▒▒▓▓▓▓▓▓▓▓▓▓▓▒▒▒▒░▒▓░░░░░▒▒▒▓▓▓▒▒░░▓██   ",
            "               ██▓▒▓▒▒▒▒▒▒▒▓░░▓▓▓▓▒▓▒▒▒▒▒▓▓▓▓▒░░░░░░░░░░░░▒░░░░░░░░░░░░░▒▓▓▓▓▓░▒▒▒▒░░░░░▓▓▒▒░░███   ",
            "               ██▓▒▓▒▒▒▒▒▒▒▓▒░░░░▒▓▒▒▒▒▓▓▓▒░░░░░░░░░░▒▒▒▒▒▒░░▒▒▒▒▒░░░░░░░░░▒▓▓▓▓▒▒▒▒░░░░░▒▓▒░▓██    ",
            "                ██▒▓▒▒▒▓▓▓▒░░░░░░░▒▒▓▓▓▒▒░▒░░░░░░░▒▒▒▒▒░░░░░▒▒▒▒▒▓▓▒░░░░░░░░░▒▓▓▓▓▒▒▒▒░░░░▒▓▒███    ",
            "                ██▓▓▓▓▓▒░▒▒░░▓▒▒▒▒▒▓▓▓█▒░░░▒▒░░▒▓▒▒░░░░░░░░░░░░░░░░▒▓▓▒░░░░░░▒░░▓▓▓▓▒▒░░░▓▓▓▓██     ",
            "                █▓▒▒▓▓▓▓▒░░░░▒▓▒▒▒▒██▓██▓░░░░░▓▒░░▒░░░░░▒▓▒░░░░░░░░░░▒▒▒▒░░░░▒▒▒░▒▓▓▓▓▒▒▒▒▓▒▓██     ",
            "               ██▓▒▒▒▓▓▓▒▒▒▒░▒▒▓▒░▒█▓▒▒██▓██▒▒▒▒▒▒▒░░░░░▒▓░░░░░░░░░░░░░▒▒▓▒░▒░▒▒▒░░▒▓▓▓▓▓▒▓▒███     ",
            "                ██▒▒▒▓▓▒▒▒▒▒▒▒▒▓▒▓▓███▓▒▒▒▒██▒░░▒░░░░░▒░▒▒░░░░▒░░▒▒▒░░░░░▒▓▒░░░░░░░░░▓▓▓▒▒▓▓██      ",
            "                ██▓▒▓▒▒▒▒▒▒▒▒▓▓▒▓░░▓█▓██████▒░░░░░▓▓░░░░▒▒░░░▒▓░░░░░░░░░░░▒▒▓░░░░░░░░░▓▓▓▒▓███      ",
            "                 ██▓▒▒▓▓▓▒▒▒▓▓▒▓▒░▒▓▒░░░░▒▓▒░░░░░▒▓░░░░░▒▒▒░░▒▓▒░░░░░░░░░░░▒▒▓░░░░░░░░░▒▓▓▒▒██      ",
            "                 ███▓▒▒▓▒▒▒▓▓▒▓▒▓░▒▒░░░░▒▒▓░░░░░░▒▒░░░░░▒▒▒▒▒▒▓▒░░▒▒▓▓░░░░░░▒▒▓░░░░░░░░░▓▓▓▓██      ",
            "                ███▓▓▓▓▓▒▒▓▓▓██▓▓▓▓▓▓░░▒▒▓░░░░░░░▓▒░░░░░░▓▒▒▒▓▓▓░▒▓▓▓▓▓▒░░░░░▒▒▒░░░░░░░░░▓▓███      ",
            "                ██▓▒▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓░░▒▓▒░░░░░░░▓▒░░░░░░▒▒▒▒▓█▓▒▓▓▓▓▓▒░░░░▒▒▒▒▓░░░░░▒▒░░▒▓██       ",
            "               ███▒▒░▓▓▓▓▓▓▓▓░░▓▓▓▓▓▒░█▓▓░░░░░░░░▓▓░░░░░░░▓▒▒█▒▒█▒░░▒▓▒░░░▓███▒▒▒░░░░░▓░░░▓███      ",
            "               ██▓▓░░░░▒▓▓▓▒▒░░░▒▓░░░▓██▒░▓░░░░░░▓▓░░░░░░░▒▒█▓▒▒▒▓▒░░░░░▒███▓▒▓█▓▒░░░░▒▒░░░▒██      ",
            "              ███▒▓░░░░░░▓▓▒░░░▒▓░░░░▒▒▓░▒▒░░░░░░▓▒▓░░░░░░▒██▒░░░▓▓▒▒▒▒▓███▓▒████▓░░░░▒▒▒░░░▓██     ",
            "              ███▓▒░░░░░░▓▓▒░░░▓▒░░░▒▒▒▓▒▒░░░░░░░█▒▓▒░░░░░░▓█▒░░░▒██▒▓████▒▓████▒▒░░░░▒▒▒░░░▒██     ",
            "              ███▓░░░░░░░▓▒▒░░▒▓░░░░▒▒▒▓▒▒░░░░░░░▓██▒░░░░░░▒█▓░▒█▓▓▓████▓▒▓███▓▒▓▒░░░░▒▒▓░░░░▓█     ",
            "              ███▒░░░░░░▒▓▒░░░▓▒░░░▒▒▒▒▓▒░░░░░░░░▒▒▒▒▒░░░░░▒▒█▒░░░░░▒██▓▒████▓▒▒▓▒░░░░▒▒▒▒░░░▒██    ",
            "              ██▓░░░░░░▒▓▒░▒░▒▓▒▒▒▒▒▒▒▒▓▒▒░░░░░░░░▒▒▒░▒░▒▒▒▒▒▓█▒░▒▒▒███▓████▒▒▒▒▓▓░░░░▒▒▒▒░░░░▓█    ",
            "              ██▒░▒░░░▓▓▒▒▒▒▒▒▓▒▒▒▒▒▒▒▓▓▓▒▒░░░░░░░░▒▒░▒▓░▒▒▒▒▒▓▓▒▒▒▓██████▓▒▓▓▓▒▒▓░░░░▒▒▒▒░░░░▒██   ",
            "             ██▒▒▓▒▓▓▓▓▓▓▓▒░░▒▓▓▒▒▒▒▒▒▒▓██▓▒░░░░░░░▒▒▒░▓█▒▒▒▒▒▒▓████▓███▓█▓▒▓▓▓▓▒▓░░░░▒▒▒░░░░░░██   ",
            "          ████▒█▓░░░▒░░░▒▒▒░░░▒▓▓▒▒▒▒▓▒▓██▓▓▒▒▒▒▒▒▒▒▓▒▒░█▓▒░▒▒▒▒▓███▓▓█▒▓▓██▓▒▒▓▓▓░░░▒▒▒▒░░░░▓▒▒██  ",
            "         ███████░░░░▒░░░▒▒▓░░░▒▒▓▓▓▓▒▓███████▓░▒▒▒▒▒▓▓▒▒▓█▒▒▒▒░▓▓▓██▓█▓▒░░▓▓▓█▒▒▒▓▒░▒▒▒▒░░░░░▓▓░██  ",
            "             ██▒░░░░▒▒░░▒▒▓▓▓▓▓█▒▓▓▒▒█▓▒████▓▒▓▓▒░▒▒▒███▓▓█▒░░▒▒█████▓▓█████▒▒██▓▓▒▒▒▒▒▒░░░░▒▓▒▒▓█  ",
            "            ███░▒▒░░▒▒▒▒▒▒▒▓▒▒▒▓░░▒▓▒▒█▒▓█▓▓████▓██▓▒░▒▒▒▒▒▒░░░░████▓▒▒█████▒░░▓█▓▒▒▒▒▒▒▒░░▒▒▒▒░▓█  ",
            "            ██▓▒▓▒░▒▓▒▒▒▒▒▒▒▓░▒▓▒░▒█▒▒▒████▓▓▓▓▓▓██▓░░░░░░░░░░░░▓█▓▓▓▓▓▓▓▓██▒▒▒██▒▒▒▒▒▓▒▒▒▒▒▓▓▒▒██  ",
            "            ██▓▒▓▒▒░▒▒▒▒▒▓▒▒▒▓▓▓▒░▒█▓▓▓▒░▒▒▓▓▓█▓▒▒▒░░░░░░░░░▓▒░░░▓██████████▓▒██▓▒▒▒▒▓▒▒▒▒░░▒▒▒███  ",
            "            ███▓░▒▒░░▓▒▒▒▒▓▓▓▓▓▓██▓█▓▒░░▒▓██▒▒▒░░░░░░░░░░░░░▒▒░░░░▒▒▒▒▒▒▓▓▒░▒▓█▓▒▓▒▒▓▒▒▒▒░▒▒▒▒▓██   ",
            "              ███▒▒▒▒▒▓▓▒▒▒▓▓▒▒▒▒░░▒▓░▒▓▒▒▓▓▒▒░░░░░░░░░░░░░░░░░░░░░░░▓▒▒▒▒▒▒▓██▓▒▒▓▒▒▒▒▒▒▒▒▒▒███    ",
            "                 ███▒▒▒▓▓▓▓▓▓██▒▒░░▒▒░▒▓▒▒▒░░▓▒░░░░░▒▓▓▒▓▓▓▓▒░░░░░░░░▒▓▓▒░▒▓▓█▒▓▓▓▒▒▒▓▓▒▒▒▓█████    ",
            "                   ██▓▓▒▒▓▓▒▒▓▒▒▒░░▒▒░▒▒░░░▒▒▒█░░░░░▒▓▒▒▒▒▒▓▓▓▓░░░░░░░▒▓▓▒▒▒█▒▒▒▒▒▒▓▓▓▓▓█████▓█     ",
            "                    ██▒▒▓▒▒▒▒▒▓▓▒░▒░▒░▒▒░░░▒▓▓▒░░░░░░▓▒▒▒▒▒▒▓▓▒░░░░░░░░▓▒▒██▓▒▒▒▒▒▓███    ██▒██     ",
            "                     ██▒▒▓▒▒▒▓▒▒▓▓░░░░░░░░░░░▒█▓░░░░░░▒▒▒▒▒▒▒▒░░░░░░░░░▓█▒▒█▒▒▓▒▒███     █▓▒▓█      ",
            "                      ███▓▓▒▒▒▓▓▒██▒░░░░░░░░░░░▒█▓▒░░░░░▒▒▒░░░░░░░▒▓▓█▓▓▓█▒▒▓▓▒▓███    ██▓▒▓█       ",
            "            ████████████ ████▓▓█▓▓█▓▓▒░░░░░░░░░░░▓▓▓▒▒░░▒▒░░▒▒▓██▓▓▓▓▓▒▒▒▓▓▒▒▓██      ██▒▒▒██       ",
            "          ██▒███▒░░░░░▒███    █████████▒░░░░░░░░░▒█▒▓▓▓▓▓▓▓▓████████████▓███▒░▒██    ██▒▒▒██        ",
            "      ████▓░░░░░░░░░░░░░▒▓██       ██████▒░░░░░░░██▓▒▒▒▓▓▓▓███▓▓█████ ████ █▓▒░▒██  ██▒░░▓█         ",
            "    ███▓▒░░░░░░░░░░░░░░▒▒▓███  ██████████▓▒▒░░░░▓███▓▓█▓▓██████▓▒▒▓███      ██▒▒▒▓███▒▒▒▓█          ",
            "   ███▒░░░░░▒░░░░░▒▒▒▒██████   ████▓████████▓▒▒▒██▓████▓████████▓▒░░▒█▓      ▓█▒▒▒▓▓▒▒▒▓█           ",
            "   ██▓░░░▒▒▒▒▒▒▒▒▒▒▒▒▒▒▓███████████▓█████████████████▓▒▓▓▓▓▓███▒░░░▒▒███      ██▒▓▓██▒▓█            ",
            "   ██▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▓██ ██████████████████████████▓▓░░▒▒▒▒▒▓▓░░░░░░▒█▓█████   ██▓▓▓▓██             ",
            "   ██▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒██ ███████████████████████████▓█░░░▒▒▒▒█▓░░░░░░░▓█████▓▓█████████▓█             ",
            "   ██▒▒▒▒▒▒▒▒▒▒▒▒▒▓████████████████████████████████▒░░▒▒▒▓▓░░░░░░░░░█████████▓▓▒▓▓█▓▓▓████          ",
            "   ██▓▒▒▒▒▒▒▒▒▒▒▒▓██████████████████████████████████▒░▒▓█▓▒░░░░▒░░░▒██████████▒▒▒▒██▓▓▓▓▓▓▓██       ",
            "    ███▓▒▒▒▒▒▒▒▒▓████████████████████████████████████▓███████▒▓██▓░▓██████▓▓█▒░░░▓███▓█▓█▓▓▓▓█      ",
            "   ██▓▒▒▒▒▒▒▒▒▒██████████████████████████████▓████████████████▓▒▒░▒█████▓▓▓█▒▒░▒▓███▓▒▒▒▒█▓▓▓████   ",
            "   ██▒▒▒▒▒▒▒▒██████████████████████████████▓███████████████████▓░▒██████▓▓▓█▓▒▒██▓▓░░░▒▒▒█▓▓█▓▒▒▓██ ",
        )
    
    # Clear the console
    clear_screen()

    # Show initial loading bar
    print("Loading System Components...")

    # Print the logo with animation
    for i, line in enumerate(logo_lines):
        # Calculate progress percentage
        progress = (i + 1) / len(logo_lines) * 100
        
        # Print the line with typing animation
        for char in line:
            sys.stdout.write(char)
            sys.stdout.flush()
            time.sleep(0.000005)  # Fast typing
        
        # Move to next line
        print("")
        
        # Update loading status at console title (if supported)
        if os.name == 'nt':  # Windows
            os.system(f"title Loading: {int(progress)}%")
    
    # Complete loading
    sys.stdout.write("\r" + " " * 80)  # Clear line
    sys.stdout.write("\rSystem Initialized Successfully! ✓\n")
    os.system(f"title {program_name} - 100% Initalized!")
    sys.stdout.flush()
    time.sleep(0.5)

    return len(logo_lines[-1]), program_name

if __name__ == "__main__":
    logo_length, program_name = print_animated_logo()
    half_logo_length = (logo_length // 2) - len(program_name)

    print(f"{'='*half_logo_length} {program_name} {'='*half_logo_length}")
    print("Press Ctrl+C at any time to exit gracefully (press twice quickly to force exit)")
    
    try:
        # Print the absolute path of the .env file
        env_path = Path('.env').absolute()
        print(f"Environment file location: {env_path}\n{'-'*logo_length}")
        
        if not create_env_file():
            print("Error: Could not create .env file")
            sys.exit(1)
        
        load_dotenv()
        
        email = os.getenv("AMAZON_EMAIL")
        password = os.getenv("AMAZON_PASSWORD")
        max_price = os.getenv("MAX_PRICE")
        product_url = os.getenv("PRODUCT_URL", "https://www.amazon.com/MSI-GeForce-5090-Gaming-Trio/dp/B0DT6Q3BXM")
        
        if not email or not password or not product_url:
            print("Error: Required environment variables missing from .env file")
            sys.exit(1)
        
        # Create a single bot instance
        checker = None
        try:
            checker = AmazonUltraFastBot(
                product_url=product_url,
                email=email,
                password=password,
                max_price=float(max_price),
                check_interval=0.05
            )
            
            # Run the monitor method
            checker.monitor()
        except KeyboardInterrupt:
            print("\nExiting program...")
        finally:
            # Make sure cleanup happens if we exit the try block for any reason
            if checker:
                checker.cleanup()
    except Exception as e:
        print(f"Unexpected error: {e}")
        sys.exit(1)