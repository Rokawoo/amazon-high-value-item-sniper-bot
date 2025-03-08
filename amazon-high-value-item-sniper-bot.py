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
from concurrent.futures import ThreadPoolExecutor, as_completed
import asyncio
import aiohttp

exit_requested = False
exit_in_progress = False

class SuppressOutput:
    """Context manager to temporarily suppress stdout and stderr output."""
    
    def __enter__(self) -> 'SuppressOutput':
        """Set up output suppression by redirecting stdout and stderr."""
        self._original_stdout = sys.stdout
        self._original_stderr = sys.stderr
        sys.stdout = open(os.devnull, 'w')
        sys.stderr = open(os.devnull, 'w')
        return self

    def __exit__(self, exc_type: Optional[type], exc_val: Optional[Exception], exc_tb: Optional[Any]) -> None:
        """Restore original stdout and stderr when exiting the context."""
        sys.stdout.close()
        sys.stderr.close()
        sys.stdout = self._original_stdout
        sys.stderr = self._original_stderr

    @staticmethod
    def update_terminal_line(message: str) -> None:
        """Update the current terminal line with a new message."""
        sys.stdout.write('\r' + ' ' * 100)
        sys.stdout.write('\r' + message)
        sys.stdout.flush()

    @staticmethod
    def update_multiple_lines(messages: List[str], prev_line_count: int = 0) -> int:
        """Update multiple lines in the terminal with new messages."""
        if prev_line_count > 0:
            sys.stdout.write('\r')
            sys.stdout.write(f'\033[{prev_line_count - 1}A')
            sys.stdout.write('\033[J')
        
        print('\n'.join(messages), end='')
        sys.stdout.flush()
        
        return len(messages)

class AmazonUltraFastBot:
    """
    Ultra-fast bot for monitoring Amazon product availability and automatic purchase.
    
    This bot continuously monitors a specified Amazon product URL for stock availability
    and automatically attempts to purchase the item when it becomes available at or below
    the specified maximum price using multiple concurrent strategies to optimize success rate.
    """
    
    def __init__(self, product_url: str, email: str, password: str, max_price: float, check_interval: float = 0.05) -> None:
        """Initialize the Amazon Ultra Fast Bot."""
        self.product_url = product_url
        self.email = email
        self.password = password
        self.max_price = float(max_price)
        self.check_interval = check_interval
        self.purchase_record_file = 'purchase_record.json'
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/96.0.4664.110 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
            'Connection': 'keep-alive',
            'Cache-Control': 'no-cache',
            'Pragma': 'no-cache'
        }
        self.load_purchase_record()
        self.purchase_attempted = False
        self.purchase_successful = False
        self.driver = None
        self.api_session = self._create_optimized_session()
        self.browser_session = self._create_optimized_session()
        self.current_price = None
        self.price_source = None
        self.status_messages = []
        self.prev_line_count = 0
        self.in_stock_prices = []
        self.price_patterns = self._compile_price_patterns()

        self.last_status_time = time.time()
        self.check_count = 0
        self.exit_requested = False
        self.browser_pid = None
        self.api_pool = ThreadPoolExecutor(max_workers=4)
        self.purchase_pool = ThreadPoolExecutor(max_workers=6)
        
        signal.signal(signal.SIGINT, self.signal_handler)
        signal.signal(signal.SIGTERM, self.signal_handler)
        atexit.register(self.cleanup)
        self.initialize_browser()
    
    def _create_optimized_session(self) -> requests.Session:
        """Create an optimized requests session with connection pooling."""
        session = requests.Session()
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=10,
            pool_maxsize=20,
            max_retries=3
        )
        session.mount('https://', adapter)
        session.mount('http://', adapter)
        session.headers.update(self.headers)
        return session
    
    def _compile_price_patterns(self) -> Tuple[re.Pattern, ...]:
        """Compile regular expression patterns for price extraction."""
        return (
            re.compile(r'\$([0-9,]+\.[0-9]{2})'),
            re.compile(r'\$([0-9,]+)'),
            re.compile(r'([0-9,]+\.[0-9]{2})\s*\$'),
            re.compile(r'([0-9,]+\.[0-9]{2})')
        )
    
    def update_price_status(self, price: float, source: str = "Browser") -> None:
        """Update the current price information without refreshing the display."""
        self.current_price = price
        self.price_source = source
        self.update_terminal_display()
    
    def update_terminal_display(self) -> None:
        """Update the terminal with unified display of price, status, and other information."""
        display_messages = []

        if self.current_price is not None:
            display_messages.append(f"Current price ({self.price_source}): ${self.current_price:.2f}")

        if hasattr(self, 'check_count') and self.check_count > 0:
            elapsed = time.time() - self.monitor_start_time
            checks_per_second = self.check_count / elapsed if elapsed > 0 else 0

            hours, minutes = divmod(int(elapsed), 3600)
            minutes, seconds = divmod(minutes, 60)
            time_formatted = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
            
            display_messages.append(f"Status: {self.check_count:,} checks | Time: {time_formatted} | Rate: {checks_per_second:.2f} checks/sec")

        if self.in_stock_prices:
            prices_str = ", ".join([f"${p:.2f}" for p in sorted(self.in_stock_prices)])
            display_messages.append(f"Detected prices: {prices_str}")
    
        self.prev_line_count = SuppressOutput.update_multiple_lines(display_messages, self.prev_line_count)
    
    def signal_handler(self, sig: int, frame: Any) -> None:
        """Handle interrupt signals with double-press detection for forced exit."""
        global exit_in_progress, exit_requested
        
        if exit_in_progress:
            print("\nExit already in progress. Please wait...")
            return
            
        current_time = time.time()
        exit_in_progress = True
        
        if hasattr(self, 'last_ctrl_c_time') and current_time - self.last_ctrl_c_time < 2:
            print("\nForce closing browser and exiting...")
            self._force_close_browser()
            print("Forcing program termination.")
            exit_requested = True
            os._exit(0)
        
        print("\nPress Ctrl+C again within 2 seconds to force exit")
        self.last_ctrl_c_time = time.time()
        
        self.cleanup()
        self._verify_browser_closed()
        
        time.sleep(0.2)
        
        exit_requested = True
        exit_in_progress = False
        
        sys.exit(0)

    def _force_close_browser(self) -> None:
        """Force close the browser window using both PID and process name approach."""
        if hasattr(self, 'driver') and self.driver:
            try:
                self.driver.quit()
            except:
                pass
        
        if hasattr(self, 'browser_pid') and self.browser_pid:
            try:
                print(f"Terminating browser process (PID: {self.browser_pid})...")
                if os.name == 'nt':
                    os.system(f'taskkill /F /PID {self.browser_pid} 2>nul')
                else:
                    os.system(f'kill -9 {self.browser_pid}')
                time.sleep(0.5)
            except:
                pass
        
        try:
            if os.name == 'nt':
                os.system('taskkill /F /IM chromedriver.exe 2>nul')
            else:
                os.system('pkill -9 -f "chromedriver"')
        except:
            pass
        
        self.driver = None
        self.browser_pid = None

    def _verify_browser_closed(self) -> None:
        """Verify that the browser has actually been closed and clean up if needed."""
        if hasattr(self, 'browser_pid') and self.browser_pid:
            try:
                if os.name == 'nt':
                    import subprocess
                    process_check = subprocess.run(f'tasklist /FI "PID eq {self.browser_pid}" /NH', 
                                               shell=True, 
                                               capture_output=True, 
                                               text=True)
                    if str(self.browser_pid) in process_check.stdout:
                        print("Browser still running, forcing termination...")
                        os.system(f'taskkill /F /PID {self.browser_pid} 2>nul')
                else:
                    import subprocess
                    process_check = subprocess.run(f'ps -p {self.browser_pid}', 
                                               shell=True, 
                                               capture_output=True, 
                                               text=True)
                    if str(self.browser_pid) in process_check.stdout:
                        print("Browser still running, forcing termination...")
                        os.system(f'kill -9 {self.browser_pid}')
            except:
                pass
            
        self.driver = None
        self.browser_pid = None

    def cleanup(self) -> None:
        """Safely clean up resources, checking if browser is already closed."""
        self.exit_requested = True
        
        if not hasattr(self, 'driver') or self.driver is None:
            return
            
        print("Closing browser...")
        
        try:
            self.driver.quit()
            print("Browser closed successfully.")
        except Exception as e:
            print(f"Error closing browser via driver: {e}")
            if hasattr(self, 'browser_pid') and self.browser_pid:
                try:
                    if os.name == 'nt':
                        os.system(f'taskkill /F /PID {self.browser_pid} 2>nul')
                    else:
                        os.system(f'kill -9 {self.browser_pid}')
                    print(f"Terminated browser process (PID: {self.browser_pid})")
                except:
                    pass
        
        try:
            self.api_pool.shutdown(wait=False)
            self.purchase_pool.shutdown(wait=False)
        except:
            pass
            
        self.driver = None
        self.browser_pid = None
        
    def load_purchase_record(self) -> None:
        """Load purchase history from file or create a new one if it doesn't exist."""
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
        """Check if the current product has already been purchased."""
        return self.product_url in self.purchase_record
        
    def mark_as_purchased(self) -> None:
        """Mark the current product as purchased in the purchase record."""
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
        """Initialize Chrome browser with ultra-optimized settings for fastest automated checkout."""
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
                "--disable-web-security",
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
                "--disable-threaded-animation",
                "--disable-threaded-scrolling",
                "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/96.0.4664.110 Safari/537.36"
            )
            
            for arg in chrome_args:
                chrome_options.add_argument(arg)
            
            chrome_prefs = {
                "profile.default_content_setting_values.notifications": 2,
                "profile.managed_default_content_settings.images": 1,
                "disk-cache-size": 4096,
                "safebrowsing.enabled": False,
                "profile.managed_default_content_settings.javascript": 1,
                "profile.default_content_setting_values.cookies": 1,
                "profile.managed_default_content_settings.plugins": 1,
                "profile.default_content_setting_values.popups": 2,
                "profile.managed_default_content_settings.geolocation": 2,
                "profile.managed_default_content_settings.media_stream": 2,
                "profile.managed_default_content_settings.automatic_downloads": 1,
                "download.prompt_for_download": False,
                "download.directory_upgrade": True,
                "credentials_enable_service": False,
                "password_manager_enabled": False,
                "profile.password_manager_enabled": False
            }
            chrome_options.add_experimental_option("prefs", chrome_prefs)
            
            chrome_options.add_experimental_option('excludeSwitches', ('enable-logging', 'enable-automation'))
            chrome_options.add_experimental_option('useAutomationExtension', False)
            
            try:
                import undetected_chromedriver as uc
                self.driver = uc.Chrome(options=chrome_options)
            except ImportError:
                service = Service(ChromeDriverManager().install())
                self.driver = webdriver.Chrome(service=service, options=chrome_options)

            try:
                if hasattr(self.driver.service, 'process') and self.driver.service.process:
                    self.browser_pid = self.driver.service.process.pid
            except:
                pass
            
            self.driver.set_page_load_timeout(10)
            self.driver.set_script_timeout(3)
            self.driver.implicitly_wait(0.1)
            
            print("Browser initialized. Logging in to Amazon...")
            self.login()
            self.preload_checkout_paths()
    
    def preload_checkout_paths(self) -> None:
        """Pre-load checkout-related pages to improve purchase speed and prepare JS code."""
        print("Pre-loading checkout paths to improve speed...")
        try:
            self.driver.get("https://www.amazon.com/gp/cart/view.html")
            self.driver.get("https://www.amazon.com/gp/checkout/select")
            self.driver.get(self.product_url)
            
            # Updated one-click JavaScript to handle popup modals
            self.one_click_js = """
            // Try to trigger Buy Now first (highest priority)
            const buyNowBtn = document.getElementById('buy-now-button');
            if (buyNowBtn) {
                buyNowBtn.click();
                
                // Set up a handler to click Place Order when popup appears
                setTimeout(() => {
                    // Check for modal/popup
                    const isModalActive = document.querySelector('body').classList.contains('a-modal-active');
                    if (isModalActive) {
                        // Try to find the Place Order button in the modal
                        const modal = document.querySelector('.a-modal-active .a-popover-wrapper, .turbo-checkout-modal, .buy-now-modal');
                        if (modal) {
                            // Try various selectors for the Place Order button
                            const buttonSelectors = [
                                '#turbo-checkout-pyo-button',
                                '.a-button-primary',
                                'button[type="submit"]',
                                'input[type="submit"]',
                                'span:contains("Place your order")'
                            ];
                            
                            for (const selector of buttonSelectors) {
                                const btn = modal.querySelector(selector);
                                if (btn) {
                                    btn.click();
                                    return;
                                }
                            }
                            
                            // If none of the specific selectors worked, try the primary button
                            const primaryButton = modal.querySelector('.a-button-primary');
                            if (primaryButton) {
                                primaryButton.click();
                            }
                        }
                    } else {
                        // If no modal, try the regular place order button
                        const placeOrder = document.getElementById('placeYourOrder') || 
                                        document.getElementById('turbo-checkout-pyo-button') || 
                                        document.getElementById('submitOrderButtonId');
                        if (placeOrder) placeOrder.click();
                    }
                }, 1000);
                
                return true;
            }
            
            // Add to Cart fallback remains the same
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
                }, 500);
                
                return true;
            }
            
            return false;
            """
            
            print("Checkout paths cached for speed")
        except Exception:
            print("Failed to pre-load checkout paths. Will continue anyway.")
            self.driver.get(self.product_url)
    
    def login(self) -> None:
        """Log in to Amazon using the provided credentials, handling 2FA if needed."""
        try:
            self.driver.get("https://www.amazon.com/ap/signin?openid.pape.max_auth_age=0&openid.return_to=https%3A%2F%2Fwww.amazon.com%2F%3Fref_%3Dnav_signin&openid.identity=http%3A%2F%2Fspecs.openid.net%2Fauth%2F2.0%2Fidentifier_select&openid.assoc_handle=usflex&openid.mode=checkid_setup&openid.claimed_id=http%3A%2F%2Fspecs.openid.net%2Fauth%2F2.0%2Fidentifier_select&openid.ns=http%3A%2F%2Fspecs.openid.net%2Fauth%2F2.0")
            
            email_field = WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.ID, "ap_email"))
            )
            email_field.clear()
            for character in self.email:
                email_field.send_keys(character)
                time.sleep(random.uniform(0.02, 0.1))
            
            continue_button = WebDriverWait(self.driver, 5).until(
                EC.element_to_be_clickable((By.ID, "continue"))
            )
            time.sleep(0.2)
            continue_button.click()
            
            password_field = WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.ID, "ap_password"))
            )
            password_field.clear()

            for character in self.password:
                password_field.send_keys(character)
                time.sleep(random.uniform(0.02, 0.1))
            
            time.sleep(0.2)
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
        """Extract price from text containing price information."""
        for pattern in self.price_patterns:
            matches = pattern.findall(text)
            if matches:
                return float(matches[0].replace(',', ''))
        return None
    
    def get_product_price(self) -> Optional[float]:
        """Get the current price of the product using various selectors."""
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
        """Check if the product is in stock and within the price limit using browser."""
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
                        self.update_price_status(price, "Browser")
                        return price <= self.max_price
            except:
                pass
                
            try:
                with self.browser_session.get(
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
                            self.update_price_status(price, "Browser")
                            return price <= self.max_price
            except:
                pass
            
            return False
            
        except Exception:
            return False

    def check_stock_via_api(self) -> bool:
        """Ultra-fast stock check using direct HTTP requests in parallel with browser checks."""
        try:
            response = self.api_session.get(
                self.product_url, 
                headers={'Cache-Control': 'no-cache, max-age=0'},
                timeout=0.5,
                stream=True
            )
            
            content = next(response.iter_content(chunk_size=10000)).decode('utf-8', errors='ignore')
            
            if "add-to-cart-button" in content and "Currently unavailable" not in content:
                stock_indicators = ("In Stock", "Only", "left in stock", "Add to Cart")
                if any(indicator in content for indicator in stock_indicators):
                    price = None
                    
                    price_match = re.search(r'\"price\":\s*\"(\$[0-9,.]+)\"', content)
                    if price_match:
                        price_str = price_match.group(1)
                        price = float(price_str.replace('$', '').replace(',', ''))
                    else:
                        for pattern in (
                            r'<span class="a-price"[^>]*><span[^>]*>([$])?([0-9,.]+)</span>',
                            r'id="priceblock_ourprice"[^>]*>([$])?([0-9,.]+)',
                            r'id="price_inside_buybox"[^>]*>([$])?([0-9,.]+)'
                        ):
                            match = re.search(pattern, content)
                            if match:
                                price_group = match.group(2) if match.group(2) else match.group(1)
                                if price_group:
                                    try:
                                        price = float(price_group.replace('$', '').replace(',', ''))
                                        break
                                    except:
                                        pass
                    
                    if price is not None:
                        self.update_price_status(price, "API")
                        return price <= self.max_price
                    else:
                        return True
            
            return False
        except Exception:
            return False
    
    def ultra_fast_purchase(self) -> bool:
        """Execute ultra-fast purchase using multiple parallel strategies with high priority."""
        if self.has_been_purchased() or self.purchase_attempted:
            return False
            
        self.purchase_attempted = True
        print("\n🚨 INITIATING LIGHTNING FAST CHECKOUT! 🚨")
        
        try:
            try:
                import psutil
                process = psutil.Process(os.getpid())
                if os.name == 'nt':
                    process.nice(psutil.HIGH_PRIORITY_CLASS)
                else:
                    process.nice(-10)
            except:
                pass
            
            futures = []
            strategies = [
                self.js_purchase_strategy,
                self.buy_now_strategy,
                self.cart_strategy,
                self.turbo_cart_strategy,
                self.js_purchase_strategy,  # Add duplicates of fastest strategy
                self.js_purchase_strategy
            ]
            
            for strategy in strategies:
                futures.append(self.purchase_pool.submit(strategy))
            
            max_wait = 15
            start = time.time()
            while time.time() - start < max_wait:
                if self.purchase_successful:
                    print("🔥 ORDER SUCCESSFULLY PLACED! 🔥")
                    for future in futures:
                        future.cancel()
                    
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
        """Purchase strategy using direct JavaScript execution for fastest checkout."""
        try:
            self.driver.get(self.product_url)
            
            # Wait for page to load enough that buttons would be present
            WebDriverWait(self.driver, 5).until(
                lambda d: d.execute_script('return document.readyState') in ['interactive', 'complete']
            )
            
            # Wait for buy now or add to cart buttons specifically
            try:
                WebDriverWait(self.driver, 3).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, "#buy-now-button, #add-to-cart-button"))
                )
            except:
                print("No purchase buttons found in js_purchase_strategy")
                return
                
            # Now execute the one-click JS
            added = self.driver.execute_script(self.one_click_js)
            if not added:
                print("JS execution didn't find actionable buttons")
                return
            
            # Wait for place order button on checkout page
            try:
                place_order_button = WebDriverWait(self.driver, 5).until(
                    EC.element_to_be_clickable((By.ID, "placeYourOrder"))
                )
                self.driver.execute_script("arguments[0].click();", place_order_button)
                self.mark_as_purchased()
            except:
                try:
                    # If not found, try going to checkout page directly
                    self.driver.get("https://www.amazon.com/gp/checkout/select")
                    
                    # Wait for place order button with multiple possible IDs
                    for button_id in ["placeYourOrder", "turbo-checkout-pyo-button", "submitOrderButtonId"]:
                        try:
                            place_order_button = WebDriverWait(self.driver, 3).until(
                                EC.element_to_be_clickable((By.ID, button_id))
                            )
                            self.driver.execute_script("arguments[0].click();", place_order_button)
                            print(f"Order placed with ID: {button_id}")
                            self.mark_as_purchased()
                            break
                        except:
                            continue
                except:
                    print("Failed to complete checkout in js_purchase_strategy")
        except Exception as e:
            print(f"Error in js_purchase_strategy: {e}")

    def buy_now_strategy(self) -> None:
        """Purchase strategy using Buy Now button for one-step checkout."""
        try:
            self.driver.get(self.product_url)
            
            # Wait for page to load
            WebDriverWait(self.driver, 5).until(
                lambda d: d.execute_script('return document.readyState') != 'loading'
            )
            
            # Click Buy Now button
            try:
                buy_now = WebDriverWait(self.driver, 5).until(
                    EC.element_to_be_clickable((By.ID, "buy-now-button"))
                )
                print("Buy Now button found, clicking...")
                self.driver.execute_script("arguments[0].click();", buy_now)
            except:
                print("Buy Now button not found or not clickable")
                return False
            
            # Give the popup a moment to appear
            time.sleep(0.5)
            
            # Now target the exact button structure you shared
            place_order_clicked = self.driver.execute_script("""
            // Try several specific selectors for the place order button
            
            // First try the exact ID from the HTML you shared
            const turboButton = document.getElementById('turbo-checkout-pyo-button');
            if (turboButton) {
                console.log("Found turbo-checkout-pyo-button");
                turboButton.click();
                return true;
            }
            
            // Try the parent span if the button itself can't be clicked
            const turboButtonParent = document.getElementById('turbo-checkout-place-order-button');
            if (turboButtonParent) {
                console.log("Found turbo-checkout-place-order-button");
                turboButtonParent.click();
                return true;
            }
            
            // Try finding the button by its text content
            const placeOrderButtons = Array.from(document.querySelectorAll('input[type="submit"]'))
                .filter(el => el.value && el.value.includes('Place your order'));
                
            if (placeOrderButtons.length > 0) {
                console.log("Found button with 'Place your order' text");
                placeOrderButtons[0].click();
                return true;
            }
            
            // Try finding by the announce element
            const announceElement = document.getElementById('turbo-checkout-place-order-button-announce');
            if (announceElement) {
                console.log("Found announce element, clicking parent button");
                // Navigate up to find the clickable parent
                let clickTarget = announceElement.parentElement;
                while (clickTarget && !clickTarget.classList.contains('a-button')) {
                    clickTarget = clickTarget.parentElement;
                }
                
                if (clickTarget) {
                    clickTarget.click();
                    return true;
                }
            }
            
            // Try a direct CSS selector approach
            const cssButton = document.querySelector('.a-button-primary input[type="submit"]');
            if (cssButton) {
                console.log("Found button via CSS selector");
                cssButton.click();
                return true;
            }
            
            // Last resort: Trigger a form submission if this is in a form
            const form = document.querySelector('form');
            if (form) {
                console.log("Trying form submission");
                form.submit();
                return true;
            }
            
            console.log("Could not find the place order button");
            return false;
            """)
            
            print("Place order clicked:", place_order_clicked)
            
            if place_order_clicked:
                print("Clicked Place Order button based on the exact HTML structure")
                # Give a moment for the order to process
                time.sleep(2)
                
                # Check if we're redirected to order confirmation page
                current_url = self.driver.current_url
                if "thank-you" in current_url or "order-details" in current_url or "order-confirmation" in current_url:
                    print("Order confirmed! Redirected to thank you page")
                    self.mark_as_purchased()
                    return True
                else:
                    print("Order may have been placed, but no confirmation page detected")
                    print("Current URL:", current_url)
                    # Still mark as potentially purchased to avoid repeated attempts
                    self.mark_as_purchased()
                    return True
                    
        except Exception as e:
            print(f"Error in buy_now_strategy: {e}")
            return False
    
    def cart_strategy(self) -> None:
        """Purchase strategy using Add to Cart + Express Checkout path."""
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
    
    def turbo_cart_strategy(self) -> None:
        """New ultra-optimized purchase strategy that combines direct API call and browser actions."""
        try:
            product_id_match = re.search(r'/dp/([A-Z0-9]{10})', self.product_url)
            if product_id_match:
                product_id = product_id_match.group(1)
                try:
                    add_to_cart_url = f"https://www.amazon.com/gp/aws/cart/add.html?ASIN.1={product_id}&Quantity.1=1"
                    self.api_session.get(add_to_cart_url, timeout=0.5)
                except:
                    pass
                
            self.driver.get(self.product_url)
            
            self.driver.execute_script("""
            function turboCheckout() {
                const buyNowBtn = document.getElementById('buy-now-button');
                if (buyNowBtn) buyNowBtn.click();
                
                const addToCartBtn = document.getElementById('add-to-cart-button');
                if (addToCartBtn) addToCartBtn.click();
                
                setTimeout(() => {
                    window.location.href = 'https://www.amazon.com/gp/checkout/select';
                }, 300);
                
                setTimeout(() => {
                    const placeOrderBtns = document.querySelectorAll('[id*="placeYourOrder"], [id*="place-order"]');
                    placeOrderBtns.forEach(btn => btn.click());
                }, 600);
            }
            
            turboCheckout();
            """)
            
            time.sleep(0.3)
            try:
                self.driver.get("https://www.amazon.com/gp/checkout/select")
                
                place_order_button = WebDriverWait(self.driver, 3).until(
                    EC.element_to_be_clickable((By.ID, "placeYourOrder"))
                )
                self.driver.execute_script("arguments[0].click();", place_order_button)
                self.mark_as_purchased()
            except:
                pass
        except:
            pass
    
    def refresh_browser_periodically(self) -> bool:
        """Periodically refresh the browser to prevent session timeouts."""
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
    
    def async_check_stock(self) -> Tuple[bool, Optional[float]]:
        """Perform multiple stock checks in parallel using thread pool."""
        futures = [
            self.api_pool.submit(self.check_stock_via_api),
            self.api_pool.submit(self.check_stock_and_price)
        ]
        
        for future in as_completed(futures):
            try:
                result = future.result()
                if result:
                    return True, self.current_price
            except:
                pass
                
        return False, None
                
    def monitor(self) -> None:
        """Main monitoring loop with optimized multi-threaded checks for maximum performance."""
        global exit_requested
        print(f"Starting lightning-fast monitoring for: {self.product_url}")
        print(f"Maximum price set to ${self.max_price:.2f}")
        print()
        
        if self.has_been_purchased():
            print("This product has already been purchased. Monitoring canceled.")
            self.cleanup()
            return
        
        self.monitor_start_time = time.time()
        last_browser_refresh = time.time()
        browser_refresh_interval = 900
        
        api_check_counter = 0
        browser_check_counter = 0
        self.check_count = 0
        
        # Updated cart and checkout URLs
        cart_url = "https://www.amazon.com/gp/cart/view.html?ref_=nav_cart"
        
        try:
            while not self.purchase_successful and not exit_requested:
                self.check_count += 1
                api_check_counter += 1
                
                force_status_update = self.check_count % 5000 == 0
                
                if api_check_counter >= 5:
                    api_check_counter = 0
                    browser_check_counter += 1
                    
                    if browser_check_counter >= 20:
                        browser_check_counter = 0
                        
                        current_time = time.time()
                        if current_time - last_browser_refresh > browser_refresh_interval:
                            self.refresh_browser_periodically()
                            last_browser_refresh = current_time
                    
                    is_in_stock, price = self.async_check_stock()
                    if is_in_stock:
                        if price is not None:
                            if price not in self.in_stock_prices:
                                self.in_stock_prices.append(price)
                            
                            print(f"\n🚨 PRODUCT IN STOCK! Price: ${price:.2f}")
                            
                            try:
                                # Load product page
                                self.driver.get(self.product_url)
                                
                                # APPROACH 1: Buy Now (fastest path)
                                try:
                                    buy_now_button = WebDriverWait(self.driver, 3).until(
                                        EC.element_to_be_clickable((By.ID, "buy-now-button"))
                                    )
                                    
                                    self.driver.execute_script("arguments[0].click();", buy_now_button)
                                    print("Buy Now clicked")
                                    
                                    # Try to place order - look for multiple possible button IDs
                                    order_button_selectors = [
                                        "turbo-checkout-pyo-button",
                                        "placeYourOrder",
                                        "submitOrderButtonId"
                                    ]
                                    
                                    for button_id in order_button_selectors:
                                        try:
                                            place_order_button = WebDriverWait(self.driver, 2).until(
                                                EC.element_to_be_clickable((By.ID, button_id))
                                            )
                                            self.driver.execute_script("arguments[0].click();", place_order_button)
                                            print(f"Order placed via Buy Now! (Button ID: {button_id})")
                                            self.mark_as_purchased()
                                            self.cleanup()
                                            return
                                        except:
                                            continue
                                    
                                    print("Buy Now checkout reached but couldn't place order")
                                except Exception as e:
                                    print(f"Buy Now approach failed: {e}")
                                    
                                    # APPROACH 2: Add to Cart
                                    try:
                                        self.driver.get(self.product_url)
                                        
                                        add_to_cart_button = WebDriverWait(self.driver, 3).until(
                                            EC.element_to_be_clickable((By.ID, "add-to-cart-button"))
                                        )
                                        
                                        self.driver.execute_script("arguments[0].click();", add_to_cart_button)
                                        print("Added to cart")
                                        
                                        # Try to find and click "Proceed to checkout" on any popups
                                        try:
                                            WebDriverWait(self.driver, 2).until(
                                                EC.presence_of_element_located((By.ID, "attach-sidesheet-checkout-button"))
                                            )
                                            checkout_buttons = self.driver.find_elements(By.ID, "attach-sidesheet-checkout-button")
                                            if checkout_buttons:
                                                self.driver.execute_script("arguments[0].click();", checkout_buttons[0])
                                                print("Proceeding to checkout from popup")
                                            else:
                                                self.driver.get(cart_url)
                                        except:
                                            self.driver.get(cart_url)
                                        
                                        # Look for proceed to checkout button in cart
                                        proceed_selectors = [
                                            "sc-buy-box-ptc-button", 
                                            "proceed-to-checkout-action",
                                            "sc-proceed-to-checkout-btn"
                                        ]
                                        
                                        for selector_id in proceed_selectors:
                                            try:
                                                proceed_button = WebDriverWait(self.driver, 2).until(
                                                    EC.element_to_be_clickable((By.ID, selector_id))
                                                )
                                                self.driver.execute_script("arguments[0].click();", proceed_button)
                                                print(f"Proceeding to checkout (Button ID: {selector_id})")
                                                break
                                            except:
                                                continue
                                        
                                        # Look for place order button
                                        for button_id in order_button_selectors:
                                            try:
                                                place_order_button = WebDriverWait(self.driver, 3).until(
                                                    EC.element_to_be_clickable((By.ID, button_id))
                                                )
                                                self.driver.execute_script("arguments[0].click();", place_order_button)
                                                print(f"Order placed via Cart! (Button ID: {button_id})")
                                                self.mark_as_purchased()
                                                self.cleanup()
                                                return
                                            except:
                                                continue
                                    except Exception as e:
                                        print(f"Add to Cart approach failed: {e}")
                                
                            except Exception as e:
                                print(f"Error during purchase attempt: {e}")
                            
                            # Log current URL to help debug
                            try:
                                print(f"Current URL after purchase attempt: {self.driver.current_url}")
                            except:
                                pass
                            
                            # Reset purchase attempted flag to try again
                            self.purchase_attempted = False
                            print("\nContinuing to monitor for another purchase attempt...")
                            self.update_terminal_display()
                
                if force_status_update:
                    self.update_terminal_display()
                
                time.sleep(random.uniform(0.001, self.check_interval))
                    
        except KeyboardInterrupt:
            print("\nMonitoring stopped by user.")
        except Exception as e:
            print(f"\nError occurred: {str(e)}. Restarting monitoring...")
            time.sleep(5)
            if not exit_requested:
                self.monitor()
        finally:
            self.cleanup()


def create_env_file(env_path: Path) -> bool:
    """Create amazon.env file with user input if it doesn't exist."""
    if not env_path.exists():
        email = input("Enter your Amazon email: ")
        password = input("Enter your Amazon password: ")
        max_price = input("Enter maximum price: ")
        product_url = input("Enter Amazon product URL: ")
        print()
        
        with open(env_path, 'w') as f:
            f.write(f"AMAZON_EMAIL={email}\n")
            f.write(f"AMAZON_PASSWORD={password}\n")
            f.write(f"MAX_PRICE={max_price}\n")
            f.write(f"PRODUCT_URL={product_url}\n")
    
    return env_path.exists()

def print_animated_logo() -> Tuple[int, str]:
    """Prints the logo with a simple typing animation effect."""
    def clear_screen() -> None:
        if os.name == 'nt':
            os.system('cls')
        else:
            os.system('clear')

    program_name = "High-Speed Amazon Sniper Bot"

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
    
    clear_screen()

    print("Loading System Components...")

    for i, line in enumerate(logo_lines):
        progress = (i + 1) / len(logo_lines) * 100
        
        for char in line:
            sys.stdout.write(char)
            sys.stdout.flush()
            time.sleep(0.000001)
        
        print("")
        
        if os.name == 'nt':
            os.system(f"title Loading: {int(progress)}%")
    
    sys.stdout.write("\r" + " " * 80)
    sys.stdout.write("\rSystem Initialized Successfully! ✓\n")
    if os.name == 'nt':
        os.system(f"title {program_name} - 100% Initalized!")
    sys.stdout.flush()
    time.sleep(0.5)

    return len(logo_lines[-1]), program_name

if __name__ == "__main__":
    try:
        import multiprocessing
        if os.name == 'nt':  # Windows
            multiprocessing.set_start_method('spawn')
        else:
            multiprocessing.set_start_method('fork')
    except:
        pass
        
    logo_length, program_name = print_animated_logo()
    half_logo_length = ((logo_length - len(program_name)) // 2) - 2

    print(f"\n{'='*half_logo_length} {program_name} {'='*half_logo_length}")
    print("Press Ctrl+C at any time to exit gracefully (press twice quickly to force exit)")
    
    try:
        env_path = Path(__file__).parent.absolute() / 'amazon.env'
        print(f"\nConfig:\n> Environment file location: {env_path}\n{'-'*logo_length}")
        
        if not create_env_file(env_path):
            print("Error: Could not create amazon.env file")
            sys.exit(1)
        
        load_dotenv(dotenv_path=env_path, override=True)
        
        email = os.getenv("AMAZON_EMAIL")
        password = os.getenv("AMAZON_PASSWORD")
        max_price = os.getenv("MAX_PRICE")
        product_url = os.getenv("PRODUCT_URL")
        
        if not email or not password or not product_url:
            print("Error: Required environment variables missing from amazon.env file")
            sys.exit(1)
        
        checker = None
        try:
            checker = AmazonUltraFastBot(
                product_url=product_url,
                email=email,
                password=password,
                max_price=float(max_price),
                check_interval=0.05
            )
            
            checker.monitor()
        except KeyboardInterrupt:
            print("\nExiting program...")
        finally:
            if checker:
                checker.cleanup()
    except Exception as e:
        print(f"Unexpected error: {e}")
        sys.exit(1)