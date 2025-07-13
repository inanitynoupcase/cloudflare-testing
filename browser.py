import asyncio
from time import time
import os
import random
import platform
import aiohttp 

from patchright.async_api import async_playwright
from loguru import logger
from dotenv import load_dotenv

# Only import proxystr if available
try:
    from proxystr import Proxy
except ImportError:
    Proxy = None

# Only import GUI dependencies if on correct platform
try:
    if platform.system() == "Linux":
        # Try to import without failing
        import cv2
        import numpy as np
        # Skip pyautogui on headless systems
        if os.environ.get('DISPLAY'):
            import pyautogui
        else:
            pyautogui = None
    else:
        cv2 = None
        np = None
        pyautogui = None
except ImportError:
    cv2 = None
    np = None
    pyautogui = None

from source import Singleton
from models import CaptchaTask

load_dotenv()

class LinuxWindowGridManager:
    """Linux-compatible window grid manager"""
    
    def __init__(self, window_width=500, window_height=200, vertical_overlap=60):
        self.window_width = window_width
        self.window_height = window_height
        self.vertical_step = window_height - vertical_overlap

        screen_width, screen_height = self.get_screen_size()
        self.cols = max(1, screen_width // window_width)
        self.rows = max(1, screen_height // self.vertical_step)

        self.grid = []
        self._generate_grid()

    def get_screen_size(self):
        """Get screen size for Linux"""
        try:
            if platform.system() == "Linux":
                # Try multiple methods for Linux
                
                # Method 1: Use xrandr if available
                if os.environ.get('DISPLAY'):
                    try:
                        import subprocess
                        result = subprocess.run(['xrandr'], capture_output=True, text=True, timeout=5)
                        for line in result.stdout.split('\n'):
                            if ' connected primary ' in line or ' connected ' in line:
                                parts = line.split()
                                for part in parts:
                                    if 'x' in part and '+' in part:
                                        resolution = part.split('+')[0]
                                        width, height = map(int, resolution.split('x'))
                                        logger.debug(f"Screen size from xrandr: {width}x{height}")
                                        return width, height
                    except Exception as e:
                        logger.debug(f"xrandr failed: {e}")
                
                # Method 2: Use environment variables if set
                if os.environ.get('SCREEN_WIDTH') and os.environ.get('SCREEN_HEIGHT'):
                    width = int(os.environ.get('SCREEN_WIDTH'))
                    height = int(os.environ.get('SCREEN_HEIGHT'))
                    logger.debug(f"Screen size from env: {width}x{height}")
                    return width, height
                
                # Method 3: Try tkinter (if available and display exists)
                if os.environ.get('DISPLAY'):
                    try:
                        import tkinter as tk
                        root = tk.Tk()
                        width = root.winfo_screenwidth()
                        height = root.winfo_screenheight()
                        root.destroy()
                        logger.debug(f"Screen size from tkinter: {width}x{height}")
                        return width, height
                    except Exception as e:
                        logger.debug(f"tkinter failed: {e}")
            
            # Fallback: Default resolution
            logger.warning("Could not detect screen size, using default 1920x1080")
            return 1920, 1080
            
        except Exception as e:
            logger.error(f"Error getting screen size: {e}")
            return 1920, 1080

    def _generate_grid(self):
        index = 0
        for row in range(self.rows):
            for col in range(self.cols):
                self.grid.append({
                    "id": index,
                    "x": col * self.window_width,
                    "y": row * self.vertical_step,
                    "is_occupied": False
                })
                index += 1

    def get_free_position(self):
        for pos in self.grid:
            if not pos["is_occupied"]:
                pos["is_occupied"] = True
                return pos
        
        # If no free positions, create a new one
        logger.warning("No free grid positions, creating new position")
        new_pos = {
            "id": len(self.grid),
            "x": random.randint(0, 100),
            "y": random.randint(0, 100),
            "is_occupied": True
        }
        self.grid.append(new_pos)
        return new_pos

    def release_position(self, pos_id):
        for pos in self.grid:
            if pos["id"] == pos_id:
                pos["is_occupied"] = False
                return
        logger.warning(f"Position {pos_id} not found in grid")

    def reset(self):
        for pos in self.grid:
            pos["is_occupied"] = False

class BrowserHandler(metaclass=Singleton):
    def __init__(self):
        self.playwright = None
        self.browser = None
        self.proxy = self.read_proxy()
        self.window_manager = LinuxWindowGridManager()
        self.headless = self._should_run_headless()
        self.proxy_config = {
            "api_key": os.getenv('KIOT_PROXY_KEY'),
            "region": os.getenv('PROXY_REGION', 'random'),
            "proxy": None,
            "ttc": 59,
            "last_fetch": 0,
            "lock": asyncio.Lock()
        }
        self.proxy_task = None
        self.browser_processes = set()  # Track browser processes
        self.last_cleanup = time()
        
    def cleanup_zombie_processes(self):
        """Kill zombie browser processes"""
        try:
            current_time = time()
            if current_time - self.last_cleanup < 60:  # Cleanup every minute
                return
                
            for proc in psutil.process_iter(['pid', 'name', 'create_time']):
                try:
                    if proc.info['name'] in ['chrome', 'chromium', 'chromium-browser']:
                        # Kill processes older than 5 minutes
                        if current_time - proc.info['create_time'] > 300:
                            logger.warning(f"Killing zombie browser process {proc.info['pid']}")
                            proc.kill()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
                    
            self.last_cleanup = current_time
        except Exception as e:
            logger.debug(f"Cleanup error: {e}")

    async def launch(self):
        """Launch browser with improved error handling and cleanup"""
        try:
            # Cleanup old processes first
            self.cleanup_zombie_processes()
            
            if not self.proxy_task:
                self.proxy_task = asyncio.create_task(self._refresh_proxy_periodically())
                
            if self.playwright:
                try:
                    await self.playwright.stop()
                except:
                    pass
                    
            self.playwright = await async_playwright().start()
            
            # Browser arguments with better stability
            args = [
                '--disable-blink-features=AutomationControlled',
                '--no-sandbox',
                '--disable-dev-shm-usage',
                '--disable-extensions',
                '--disable-plugins',
                '--disable-images',  # Speed up loading
                '--disable-javascript',  # We'll enable it selectively
                '--memory-pressure-off',
                '--max_old_space_size=512',  # Limit memory
            ]
            
            if not self.headless:
                args.extend([
                    "--window-size=500,200",
                    "--window-position=0,0"
                ])
            else:
                args.extend([
                    '--disable-gpu',
                    '--disable-web-security',
                    '--window-size=1920,1080'
                ])
            
            launch_options = {
                'headless': self.headless,
                'args': args,
                'timeout': 30000,  # 30 second timeout
            }
            
            if self.proxy:
                launch_options['proxy'] = self.proxy
                
            # Launch with timeout
            try:
                self.browser = await asyncio.wait_for(
                    self.playwright.chromium.launch(**launch_options),
                    timeout=30
                )
                logger.success("Browser launched successfully")
            except asyncio.TimeoutError:
                logger.error("Browser launch timeout")
                raise
                
        except Exception as e:
            logger.error(f"Failed to launch browser: {e}")
            await self.cleanup_all()
            raise

    async def get_page(self):
        """Get page with timeout and retry logic"""
        max_retries = 3
        for attempt in range(max_retries):
            try:
                if not self.playwright or not self.browser:
                    await self.launch()

                # Create context with timeout
                config = self.proxy_config
                context_options = {'timeout': 30000}
                
                async with config["lock"]:
                    if config["proxy"]:
                        context_options["proxy"] = {"server": config["proxy"]}

                if not self.headless:
                    context_options['viewport'] = {"width": 500, "height": 100}
                else:
                    context_options['viewport'] = {"width": 1920, "height": 1080}
                    
                context = await self.browser.new_context(**context_options)
                
                # Set timeout for all pages in context
                context.set_default_timeout(30000)
                context.set_default_navigation_timeout(30000)

    async def close(self):
        try:
            if self.proxy_task:
                self.proxy_task.cancel()
                try:
                    await self.proxy_task
                except asyncio.CancelledError:
                    pass
                self.proxy_task = None
                
            if self.browser:
                try:
                    await asyncio.wait_for(self.browser.close(), timeout=10)
                except:
                    pass
                self.browser = None
                
            if self.playwright:
                try:
                    await asyncio.wait_for(self.playwright.stop(), timeout=10)
                except:
                    pass
                self.playwright = None
                
            # Force cleanup zombie processes
            self.cleanup_zombie_processes()
            
        except Exception as e:
            logger.error(f"Cleanup error: {e}")

    async def close_page(self, page):
        """Improved page cleanup"""
        try:
            if hasattr(page, '_grid_position_id'):
                self.window_manager.release_position(page._grid_position_id)
                
            # Close with timeout
            if page:
                await asyncio.wait_for(page.close(), timeout=5)
            if page.context:
                await asyncio.wait_for(page.context.close(), timeout=5)
                
        except Exception as e:
            logger.debug(f"Error closing page: {e}")

class Browser:
    def __init__(self, page=None):
        self.page = page
        self.lock = asyncio.Lock()

    async def solve_captcha(self, task: CaptchaTask):
        """Improved solve_captcha with proper timeout and cleanup"""
        page = None
        try:
            async with asyncio.timeout(120):  # 2 minute total timeout
                page = await BrowserHandler().get_page()
                
                # Determine if we can use advanced features
                handler = BrowserHandler()
                use_advanced_features = not handler.headless and cv2 and pyautogui
                
                if use_advanced_features:
                    await self.block_rendering()
                    
                logger.debug(f"Navigating to {task.websiteURL}")
                await page.goto(task.websiteURL, timeout=30000)
                
                if use_advanced_features:
                    await self.unblock_rendering()
                    
                await self.load_captcha(page, websiteKey=task.websiteKey)
                return await self.wait_for_turnstile_token(page, use_advanced_features)
                
        except asyncio.TimeoutError:
            logger.error("Captcha solving timeout")
            return None
        except Exception as e:
            logger.error(f"Error in solve_captcha: {e}")
            return None
        finally:
            if page:
                await BrowserHandler().close_page(page)

    async def load_captcha(self, page, websiteKey: str = '0x4AAAAAAA0SGzxWuGl6kriB', action: str = ''):
        """Load captcha with improved script"""
        script = f"""
        // Remove previous captcha if exists
        const existing = document.querySelector('#captcha-overlay');
        if (existing) existing.remove();

        // Create overlay
        const overlay = document.createElement('div');
        overlay.id = 'captcha-overlay';
        overlay.style.position = 'fixed';
        overlay.style.top = '0';
        overlay.style.left = '0';
        overlay.style.width = '100vw';
        overlay.style.height = '100vh';
        overlay.style.backgroundColor = 'rgba(0, 0, 0, 0.5)';
        overlay.style.display = 'flex';
        overlay.style.justifyContent = 'center';
        overlay.style.alignItems = 'center';
        overlay.style.zIndex = '1000';

        // Add captcha widget
        const captchaDiv = document.createElement('div');
        captchaDiv.className = 'cf-turnstile';
        captchaDiv.setAttribute('data-sitekey', '{websiteKey}');
        captchaDiv.setAttribute('data-callback', 'onCaptchaSuccess');
        captchaDiv.setAttribute('data-action', '{action}');

        overlay.appendChild(captchaDiv);
        document.body.appendChild(overlay);

        // Callback function
        window.onCaptchaSuccess = function(token) {{
            console.log('Captcha solved:', token);
        }};

        // Load Cloudflare Turnstile script
        if (!document.querySelector('script[src*="turnstile"]')) {{
            const script = document.createElement('script');
            script.src = 'https://challenges.cloudflare.com/turnstile/v0/api.js';
            script.async = true;
            script.defer = true;
            document.head.appendChild(script);
        }}
        """

        await page.evaluate(script)

    async def wait_for_turnstile_token(self, page, use_advanced_features=False) -> str | None:
        """Improved token waiting with better error handling"""
        try:
            locator = page.locator('input[name="cf-turnstile-response"]')

            token = ""
            start_time = time()
            timeout = 90  # 90 seconds timeout
            click_attempted = False
            
            while not token and (time() - start_time) < timeout:
                await asyncio.sleep(1)
                try:
                    token = await locator.input_value(timeout=1000)
                    
                    if not token and not click_attempted:
                        # Try clicking the widget once
                        try:
                            widget = page.locator('.cf-turnstile')
                            if await widget.is_visible(timeout=2000):
                                await widget.click(timeout=2000)
                                click_attempted = True
                                logger.debug('Widget click performed')
                        except:
                            pass
                            
                except Exception as er:
                    logger.debug(f'Token check error: {er}')
                    
                if token:
                    logger.debug(f'Got captcha token: {token[:50]}...')
                    break
                    
            if not token:
                logger.warning('Token not found within timeout')
                
            return token
            
        except Exception as e:
            logger.error(f"Error waiting for token: {e}")
            return None
            
class Browser:
    def __init__(self, page=None):
        self.page = page
        self.lock = asyncio.Lock()

    async def solve_captcha(self, task: CaptchaTask):
        if not self.page:
            self.page = await BrowserHandler().get_page()

        async with self.lock:
            try:
                # Determine if we can use advanced features
                handler = BrowserHandler()
                use_advanced_features = not handler.headless and cv2 and pyautogui
                
                if use_advanced_features:
                    await self.block_rendering()
                    
                await self.page.goto(task.websiteURL)
                
                if use_advanced_features:
                    await self.unblock_rendering()
                    
                await self.load_captcha(websiteKey=task.websiteKey)
                return await self.wait_for_turnstile_token(use_advanced_features)
                
            except Exception as e:
                logger.error(f"Error in solve_captcha: {e}")
                return None
            finally:
                await BrowserHandler().close_page(self.page)
                self.page = None

    async def load_captcha(self, websiteKey: str = '0x4AAAAAAA0SGzxWuGl6kriB', action: str = ''):
        script = f"""
        // Remove previous captcha if exists
        const existing = document.querySelector('#captcha-overlay');
        if (existing) existing.remove();

        // Create overlay
        const overlay = document.createElement('div');
        overlay.id = 'captcha-overlay';
        overlay.style.position = 'fixed';
        overlay.style.top = '0';
        overlay.style.left = '0';
        overlay.style.width = '100vw';
        overlay.style.height = '100vh';
        overlay.style.backgroundColor = 'rgba(0, 0, 0, 0.5)';
        overlay.style.display = 'flex';
        overlay.style.justifyContent = 'center';
        overlay.style.alignItems = 'center';
        overlay.style.zIndex = '1000';

        // Add captcha widget
        const captchaDiv = document.createElement('div');
        captchaDiv.className = 'cf-turnstile';
        captchaDiv.setAttribute('data-sitekey', '{websiteKey}');
        captchaDiv.setAttribute('data-callback', 'onCaptchaSuccess');
        captchaDiv.setAttribute('data-action', '{action}');

        overlay.appendChild(captchaDiv);
        document.body.appendChild(overlay);

        // Load Cloudflare Turnstile script
        if (!document.querySelector('script[src*="turnstile"]')) {{
            const script = document.createElement('script');
            script.src = 'https://challenges.cloudflare.com/turnstile/v0/api.js';
            script.async = true;
            script.defer = true;
            document.head.appendChild(script);
        }}
        """

        await self.page.evaluate(script)

    async def wait_for_turnstile_token(self, use_advanced_features=False) -> str | None:
        locator = self.page.locator('input[name="cf-turnstile-response"]')

        token = ""
        start_time = time.time()
        timeout = 30  # 30 seconds timeout
        
        while not token and (time.time() - start_time) < timeout:
            await asyncio.sleep(0.5)
            try:
                token = await locator.input_value(timeout=500)
                
                if not token and use_advanced_features:
                    # Try advanced checkbox detection
                    if await self.check_for_checkbox():
                        logger.debug('Advanced checkbox click performed')
                elif not token:
                    # Fallback: simple click attempt
                    try:
                        widget = self.page.locator('.cf-turnstile')
                        if await widget.is_visible(timeout=1000):
                            await widget.click(timeout=1000)
                            logger.debug('Simple widget click performed')
                    except:
                        pass
                        
            except Exception as er:
                logger.debug(f'Token check error: {er}')
                
            if token:
                logger.debug(f'Got captcha token: {token[:50]}...')
                break
                
        if not token:
            logger.warning('Token not found within timeout')
            
        return token

    async def check_for_checkbox(self):
        """Advanced checkbox detection using computer vision"""
        
        if not cv2 or not np or not pyautogui:
            logger.debug("CV libraries not available, skipping advanced detection")
            return False
            
        try:
            # Take screenshot
            image_bytes = await self.page.screenshot(full_page=True)
            screen_np = np.frombuffer(image_bytes, dtype=np.uint8)
            screen = cv2.imdecode(screen_np, cv2.IMREAD_COLOR)

            # Try to load template
            template_path = "screens/checkbox.png"
            if not os.path.exists(template_path):
                logger.debug("Checkbox template not found, skipping CV detection")
                return False
                
            template = cv2.imread(template_path)
            if template is None:
                logger.debug("Failed to load checkbox template")
                return False

            # Perform template matching
            result = cv2.matchTemplate(screen, template, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, max_loc = cv2.minMaxLoc(result)

            if max_val > 0.9:
                logger.debug(f"Checkbox found with confidence: {max_val}")
                h, w = template.shape[:2]
                center_x = max_loc[0] + w // 2
                center_y = max_loc[1] + h // 2
                
                # Calculate screen coordinates
                x, y = self.get_coords_to_click(self.page, center_x, center_y)
                
                # Perform click
                pyautogui.click(x, y)
                logger.success("Advanced checkbox click successful")
                return True
                
        except Exception as e:
            logger.debug(f"Advanced checkbox detection failed: {e}")
            
        return False

    def get_coords_to_click(self, page, x, y):
        """Calculate real screen coordinates for clicking"""
        try:
            if hasattr(page, '_grid_position_id'):
                handler = BrowserHandler()
                pos = handler.window_manager.grid[page._grid_position_id]
                screen_x = pos['x'] + x + random.randint(5, 10)
                screen_y = pos['y'] + y + random.randint(75, 85)
                return screen_x, screen_y
        except Exception as e:
            logger.debug(f"Failed to calculate click coordinates: {e}")
            
        # Fallback to direct coordinates
        return x, y

    async def route_handler(self, route):
        """Route handler for resource blocking"""
        blocked_extensions = ['.js', '.css', '.png', '.jpg', '.svg', '.gif', '.woff', '.ttf']
        
        if any(route.request.url.endswith(ext) for ext in blocked_extensions):
            await route.abort()
        else:
            await route.continue_()

    async def block_rendering(self):
        await self.page.route("**/*", self.route_handler)

    async def unblock_rendering(self):
        await self.page.unroute("**/*", self.route_handler)