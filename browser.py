import asyncio
from time import time
import os
import random
import platform
import aiohttp 

from patchright.async_api import async_playwright
from loguru import logger
from dotenv import load_dotenv

# Cross-platform imports với fallback
try:
    from proxystr import Proxy
except ImportError:
    Proxy = None

try:
    import psutil
except ImportError:
    psutil = None

# Cross-platform GUI imports
try:
    import cv2
    import numpy as np
    import pyautogui
    
    # Platform-specific window management
    if platform.system() == "Windows":
        try:
            import win32gui
            import win32con
            import win32api
        except ImportError:
            win32gui = None
            win32con = None
            win32api = None
    else:
        win32gui = None
        win32con = None
        win32api = None
        
except ImportError:
    cv2 = None
    np = None
    pyautogui = None
    win32gui = None
    win32con = None
    win32api = None

from source import Singleton
from models import CaptchaTask

load_dotenv()

class CrossPlatformWindowManager:
    """Cross-platform window manager that works on both Windows and Linux"""
    
    def __init__(self, window_width=500, window_height=200, overlap=60):
        self.window_width = window_width
        self.window_height = window_height
        self.overlap = overlap
        self.system = platform.system()
        
        # Get screen dimensions cross-platform
        self.screen_width, self.screen_height = self._get_screen_size()
        
        # Calculate grid
        self.cols = max(1, (self.screen_width - overlap) // (window_width - overlap))
        self.rows = max(1, (self.screen_height - overlap) // (window_height - overlap))
        
        self.grid = []
        self._generate_grid()
        
        logger.debug(f"Window manager initialized for {self.system}: {self.screen_width}x{self.screen_height}, grid: {self.cols}x{self.rows}")

    def _get_screen_size(self):
        """Get screen size cross-platform"""
        try:
            # Method 1: pyautogui (works on both platforms)
            if pyautogui:
                width, height = pyautogui.size()
                logger.debug(f"Screen size from pyautogui: {width}x{height}")
                return width, height
            
            # Method 2: Environment variables
            if os.environ.get('SCREEN_WIDTH') and os.environ.get('SCREEN_HEIGHT'):
                width = int(os.environ.get('SCREEN_WIDTH'))
                height = int(os.environ.get('SCREEN_HEIGHT'))
                logger.debug(f"Screen size from env: {width}x{height}")
                return width, height
            
            # Method 3: Platform-specific
            if self.system == "Linux":
                return self._get_linux_screen_size()
            elif self.system == "Windows":
                return self._get_windows_screen_size()
                
        except Exception as e:
            logger.debug(f"Error getting screen size: {e}")
            
        # Fallback
        logger.warning("Could not detect screen size, using default 1920x1080")
        return 1920, 1080

    def _get_linux_screen_size(self):
        """Get screen size on Linux"""
        try:
            # Try xrandr first
            if os.environ.get('DISPLAY'):
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
            
            # Try tkinter
            if os.environ.get('DISPLAY'):
                import tkinter as tk
                root = tk.Tk()
                width = root.winfo_screenwidth()
                height = root.winfo_screenheight()
                root.destroy()
                logger.debug(f"Screen size from tkinter: {width}x{height}")
                return width, height
                
        except Exception as e:
            logger.debug(f"Linux screen size detection failed: {e}")
            
        return 1920, 1080

    def _get_windows_screen_size(self):
        """Get screen size on Windows"""
        try:
            # Try win32api
            if win32api:
                width = win32api.GetSystemMetrics(0)
                height = win32api.GetSystemMetrics(1)
                logger.debug(f"Screen size from win32api: {width}x{height}")
                return width, height
            
            # Try tkinter
            import tkinter as tk
            root = tk.Tk()
            width = root.winfo_screenwidth()
            height = root.winfo_screenheight()
            root.destroy()
            logger.debug(f"Screen size from tkinter: {width}x{height}")
            return width, height
            
        except Exception as e:
            logger.debug(f"Windows screen size detection failed: {e}")
            
        return 1920, 1080

    def _generate_grid(self):
        """Generate window positions grid"""
        index = 0
        for row in range(self.rows):
            for col in range(self.cols):
                x = col * (self.window_width - self.overlap)
                y = row * (self.window_height - self.overlap)
                
                self.grid.append({
                    "id": index,
                    "x": x,
                    "y": y,
                    "is_occupied": False
                })
                index += 1

    def get_free_position(self):
        """Get next available window position"""
        for pos in self.grid:
            if not pos["is_occupied"]:
                pos["is_occupied"] = True
                return pos
        
        # If no free positions, create random one
        logger.warning("No free grid positions, using random position")
        return {
            "id": len(self.grid),
            "x": random.randint(0, 200),
            "y": random.randint(0, 200),
            "is_occupied": True
        }

    def release_position(self, pos_id):
        """Release window position"""
        for pos in self.grid:
            if pos["id"] == pos_id:
                pos["is_occupied"] = False
                return
        logger.debug(f"Position {pos_id} not found in grid")

    def reset(self):
        """Reset all positions"""
        for pos in self.grid:
            pos["is_occupied"] = False

class BrowserHandler(metaclass=Singleton):
    def __init__(self):
        # Initialize system first (needed by other methods)
        self.system = platform.system()
        
        # Initialize other attributes
        self.playwright = None
        self.browser = None
        self.proxy = self.read_proxy()
        self.window_manager = CrossPlatformWindowManager()
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
        self.last_cleanup = time()
        
        logger.info(f"Browser handler initialized for {self.system}, headless: {self.headless}")

    def _should_run_headless(self):
        """Cross-platform headless detection"""
        
        # Force headless if explicitly set
        if os.environ.get('FORCE_HEADLESS', '').lower() == 'true':
            logger.info("Forced headless mode via environment variable")
            return True
        
        # Use self.system instead of system variable
        if self.system == "Windows":
            # On Windows, check for GUI capabilities
            if not pyautogui:
                logger.info("GUI automation not available on Windows, running headless")
                return True
            logger.info("Windows GUI mode available")
            return False
            
        elif self.system == "Linux":
            # On Linux, check for X11 display
            if not os.environ.get('DISPLAY'):
                logger.info("No DISPLAY environment variable, running headless")
                return True
            if not pyautogui:
                logger.info("GUI automation not available on Linux, running headless")
                return True
            logger.info("Linux GUI mode available")
            return False
            
        else:
            # macOS or other - default to headless
            logger.info(f"Platform {self.system} - defaulting to headless")
            return True

    @staticmethod
    def read_proxy():
        """Read proxy configuration"""
        if proxy := os.getenv('PROXY'):
            if Proxy:
                try:
                    return Proxy(proxy).playwright
                except Exception as e:
                    logger.warning(f"Failed to parse proxy: {e}")
                    return None
            else:
                logger.warning("proxystr not available, proxy ignored")
                return None
        return None

    def cleanup_zombie_processes(self):
        """Cross-platform zombie process cleanup"""
        if not psutil:
            return
            
        try:
            current_time = time()
            if current_time - self.last_cleanup < 60:
                return
            
            # Cross-platform process names
            if self.system == "Windows":
                process_names = ['chrome.exe', 'chromium.exe', 'msedge.exe']
            else:
                process_names = ['chrome', 'chromium', 'chromium-browser']
            
            killed_count = 0
            for proc in psutil.process_iter(['pid', 'name', 'create_time']):
                try:
                    if proc.info['name'] in process_names:
                        # Kill processes older than 5 minutes
                        if current_time - proc.info['create_time'] > 300:
                            logger.warning(f"Killing zombie process {proc.info['pid']}: {proc.info['name']}")
                            proc.kill()
                            killed_count += 1
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
                    
            if killed_count > 0:
                logger.info(f"Cleaned up {killed_count} zombie browser processes")
                    
            self.last_cleanup = current_time
        except Exception as e:
            logger.debug(f"Cleanup error: {e}")

    async def _load_kiotproxy(self):
        """Load new proxy from KiotProxy API"""
        config = self.proxy_config
        async with config["lock"]:
            try:
                async with aiohttp.ClientSession() as session:
                    url = f"https://api.kiotproxy.com/api/v1/proxies/new?key={config['api_key']}&region={config['region']}"
                    async with session.get(url, timeout=10) as resp:
                        data = await resp.json()
                        if data["success"]:
                            proxy_data = data["data"]
                            config["proxy"] = f"http://{proxy_data['http']}"
                            config["ttc"] = proxy_data["ttc"]
                            config["last_fetch"] = time()
                            logger.success(f"Loaded proxy: {proxy_data['http']} | Location: {proxy_data['location']}")
                        else:
                            error_msg = data.get('message', 'Unknown error')
                            logger.error(f"KiotProxy API error: {error_msg}")
                            config["proxy"] = None
                            raise ValueError(error_msg)
            except aiohttp.ClientError as e:
                logger.error(f"Network error loading proxy: {str(e)}")
                raise
            except Exception as e:
                logger.error(f"Proxy loading error: {str(e)}")
                raise

    async def _refresh_proxy_periodically(self):
        """Refresh proxy periodically"""
        config = self.proxy_config
        consecutive_errors = 0

        while True:
            try:
                # Check if proxy is still valid
                if config["proxy"] and config["ttc"]:
                    time_since_fetch = time() - config["last_fetch"]
                    time_remaining = config["ttc"] - time_since_fetch
                    if time_remaining > 60:
                        logger.debug(f"Proxy valid for {time_remaining:.0f}s")
                        await asyncio.sleep(min(time_remaining - 30, 300))
                        continue

                # Load new proxy
                logger.info("Refreshing proxy...")
                await self._load_kiotproxy()
                consecutive_errors = 0

                # Wait according to TTC
                wait_time = max(config["ttc"] - 30, 60) if config["ttc"] else 300
                logger.debug(f"Next proxy refresh in {wait_time}s")
                await asyncio.sleep(wait_time)

            except Exception as e:
                consecutive_errors += 1

                if "limit" in str(e).lower() or "rate" in str(e).lower():
                    wait_time = min(300 * consecutive_errors, 1800)
                    logger.error(f"Rate limit #{consecutive_errors}, waiting {wait_time}s")
                else:
                    wait_time = min(60 * consecutive_errors, 600)
                    logger.error(f"Proxy error #{consecutive_errors}: {str(e)}, waiting {wait_time}s")

                await asyncio.sleep(wait_time)

    async def launch(self):
        """Cross-platform browser launch"""
        try:
            # Cleanup old processes first
            self.cleanup_zombie_processes()
            
            if not self.proxy_task and self.proxy_config["api_key"]:
                self.proxy_task = asyncio.create_task(self._refresh_proxy_periodically())
                
            if self.playwright:
                try:
                    await self.playwright.stop()
                except:
                    pass
                    
            self.playwright = await async_playwright().start()
            
            # Cross-platform browser arguments
            args = [
                '--disable-blink-features=AutomationControlled',
                '--no-sandbox',
                '--disable-dev-shm-usage',
                '--disable-extensions',
                '--disable-plugins',
                '--memory-pressure-off',
                '--max_old_space_size=512',
                '--disable-background-timer-throttling',
                '--disable-backgrounding-occluded-windows',
                '--disable-renderer-backgrounding',
            ]
            
            # Platform-specific optimizations
            if self.system == "Windows":
                args.extend([
                    '--disable-gpu-sandbox',
                    '--disable-software-rasterizer',
                ])
            elif self.system == "Linux":
                args.extend([
                    '--disable-gpu',
                    '--disable-software-rasterizer',
                ])
            
            # Window/headless configuration
            if not self.headless:
                args.extend([
                    f"--window-size={self.window_manager.window_width},{self.window_manager.window_height}",
                    "--window-position=0,0"
                ])
            else:
                args.extend([
                    '--disable-gpu',
                    '--window-size=1920,1080'
                ])
            
            launch_options = {
                'headless': self.headless,
                'args': args,
                'timeout': 30000,
            }
            
            if self.proxy:
                launch_options['proxy'] = self.proxy
            
            # Cross-platform browser selection with fallbacks
            browser_launched = False
            
            # Try platform-preferred browsers first
            if self.system == "Windows" and not self.headless:
                # Windows GUI: Edge -> Chrome -> Chromium
                # for channel in ['msedge', 'chrome']:
                for channel in ['chrome']:
                    try:
                        launch_options['channel'] = channel
                        self.browser = await asyncio.wait_for(
                            self.playwright.chromium.launch(**launch_options),
                            timeout=30
                        )
                        browser_launched = True
                        logger.success(f"{channel.capitalize()} browser launched successfully")
                        break
                    except Exception as e:
                        logger.debug(f"{channel} launch failed: {e}")
                        continue
            
            # Fallback to standard Chromium
            if not browser_launched:
                try:
                    launch_options.pop('channel', None)
                    self.browser = await asyncio.wait_for(
                        self.playwright.chromium.launch(**launch_options),
                        timeout=30
                    )
                    browser_launched = True
                    logger.success("Chromium browser launched successfully")
                except Exception as e:
                    logger.error(f"Chromium launch failed: {e}")
            
            # Final fallback with minimal args
            if not browser_launched:
                logger.warning("Trying minimal browser configuration...")
                minimal_args = ['--no-sandbox', '--disable-dev-shm-usage']
                if self.headless:
                    minimal_args.append('--headless')
                
                launch_options['args'] = minimal_args
                self.browser = await asyncio.wait_for(
                    self.playwright.chromium.launch(**launch_options),
                    timeout=30
                )
                logger.success("Browser launched with minimal configuration")
                    
        except asyncio.TimeoutError:
            logger.error("Browser launch timeout")
            await self.cleanup_all()
            raise
        except Exception as e:
            logger.error(f"Failed to launch browser: {e}")
            await self.cleanup_all()
            raise

    async def set_window_position(self, page, x, y):
        """Cross-platform window positioning"""
        if self.headless:
            return
            
        try:
            session = await page.context.new_cdp_session(page)
            result = await session.send("Browser.getWindowForTarget")
            window_id = result["windowId"]
            
            await session.send("Browser.setWindowBounds", {
                "windowId": window_id,
                "bounds": {
                    "left": x,
                    "top": y,
                    "width": self.window_manager.window_width,
                    "height": self.window_manager.window_height
                }
            })
            
            logger.debug(f"Window positioned at ({x}, {y}) on {self.system}")
            
        except Exception as e:
            logger.debug(f"Failed to set window position: {e}")

    async def get_page(self):
        """Cross-platform page creation with retry logic"""
        max_retries = 3
        for attempt in range(max_retries):
            try:
                if not self.playwright or not self.browser:
                    await self.launch()

                # Create context
                config = self.proxy_config
                context_options = {}
                
                async with config["lock"]:
                    if config["proxy"]:
                        context_options["proxy"] = {"server": config["proxy"]}

                if not self.headless:
                    context_options['viewport'] = {
                        "width": self.window_manager.window_width, 
                        "height": self.window_manager.window_height
                    }
                else:
                    context_options['viewport'] = {"width": 1920, "height": 1080}
                    
                context = await self.browser.new_context(**context_options)
                context.set_default_timeout(30000)
                context.set_default_navigation_timeout(30000)

                page = await context.new_page()
                
                # Set window position (cross-platform)
                if not self.headless:
                    try:
                        position = self.window_manager.get_free_position()
                        await self.set_window_position(page, position["x"], position["y"])
                        page._grid_position_id = position["id"]
                        logger.debug(f"Page positioned at grid {position['id']}")
                    except Exception as e:
                        logger.warning(f"Failed to set window position: {e}")
                        page._grid_position_id = 0
                else:
                    page._grid_position_id = 0
                    
                return page
                
            except Exception as e:
                logger.warning(f"Get page attempt {attempt + 1} failed: {e}")
                if attempt == max_retries - 1:
                    await self.cleanup_all()
                    raise
                await asyncio.sleep(1)

    async def cleanup_all(self):
        """Cross-platform complete cleanup"""
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
            
            logger.debug("Browser handler cleanup completed")
            
        except Exception as e:
            logger.error(f"Cleanup error: {e}")

    async def close_page(self, page):
        """Cross-platform page cleanup"""
        try:
            if hasattr(page, '_grid_position_id'):
                self.window_manager.release_position(page._grid_position_id)
                
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
        """Cross-platform captcha solving"""
        page = None
        try:
            async def _solve():
                nonlocal page
                page = await BrowserHandler().get_page()
                
                # Check if advanced features available
                handler = BrowserHandler()
                use_advanced_features = not handler.headless and cv2 and pyautogui
                
                if use_advanced_features:
                    await self.block_rendering(page)
                    
                logger.debug(f"Navigating to {task.websiteURL}")
                await page.goto(task.websiteURL, timeout=30000)
                
                if use_advanced_features:
                    await self.unblock_rendering(page)
                    
                await self.load_captcha(page, websiteKey=task.websiteKey)
                return await self.wait_for_turnstile_token(page, use_advanced_features)
            
            return await asyncio.wait_for(_solve(), timeout=120)
                
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
        """Load captcha widget with enhanced script"""
        script = f"""
        // Remove previous captcha if exists
        const existing = document.querySelector('#captcha-overlay');
        if (existing) existing.remove();

        // Create overlay with better styling
        const overlay = document.createElement('div');
        overlay.id = 'captcha-overlay';
        overlay.style.cssText = `
            position: fixed; top: 0; left: 0; width: 100vw; height: 100vh;
            background: rgba(0,0,0,0.5); display: flex; justify-content: center;
            align-items: center; z-index: 10000; font-family: Arial, sans-serif;
        `;

        // Create container for better centering
        const container = document.createElement('div');
        container.style.cssText = `
            background: white; padding: 30px; border-radius: 10px;
            box-shadow: 0 4px 20px rgba(0,0,0,0.3);
        `;

        // Add title
        const title = document.createElement('h3');
        title.textContent = 'Please complete the CAPTCHA';
        title.style.cssText = 'margin: 0 0 20px 0; text-align: center; color: #333;';

        // Create turnstile widget
        const widget = document.createElement('div');
        widget.className = 'cf-turnstile';
        widget.setAttribute('data-sitekey', '{websiteKey}');
        widget.setAttribute('data-callback', 'onCaptchaSuccess');
        widget.setAttribute('data-action', '{action}');
        widget.setAttribute('data-theme', 'light');
        widget.setAttribute('data-size', 'normal');
        widget.style.cssText = 'cursor: pointer; margin: 0 auto; display: block;';

        // Add loading message
        const loading = document.createElement('div');
        loading.textContent = 'Loading CAPTCHA...';
        loading.style.cssText = 'text-align: center; margin-top: 10px; color: #666;';

        container.appendChild(title);
        container.appendChild(widget);
        container.appendChild(loading);
        overlay.appendChild(container);
        document.body.appendChild(overlay);

        // Enhanced callback function
        window.onCaptchaSuccess = function(token) {{
            console.log('🎉 Captcha solved successfully!');
            
            // Hide loading message
            if (loading) loading.style.display = 'none';
            
            // Create/update hidden input
            let input = document.querySelector('input[name="cf-turnstile-response"]');
            if (!input) {{
                input = document.createElement('input');
                input.type = 'hidden';
                input.name = 'cf-turnstile-response';
                document.body.appendChild(input);
            }}
            input.value = token;
            
            // Store in multiple places for reliability
            window.captchaToken = token;
            sessionStorage.setItem('captcha-token', token);
            
            // Trigger custom event
            window.dispatchEvent(new CustomEvent('captcha-solved', {{detail: token}}));
            
            // Visual feedback
            widget.style.border = '2px solid green';
            setTimeout(() => {{
                if (overlay && overlay.parentNode) {{
                    overlay.style.opacity = '0.8';
                }}
            }}, 1000);
        }};

        // Enhanced error handling
        window.onCaptchaError = function(error) {{
            console.error('Captcha error:', error);
            loading.textContent = 'CAPTCHA error. Please refresh the page.';
            loading.style.color = 'red';
        }};

        window.onCaptchaExpired = function() {{
            console.warn('Captcha expired');
            loading.textContent = 'CAPTCHA expired. Please try again.';
            loading.style.color = 'orange';
        }};

        // Load Cloudflare Turnstile script with enhanced loading
        const loadTurnstileScript = () => {{
            if (document.querySelector('script[src*="turnstile"]')) {{
                console.log('Turnstile script already loaded');
                return;
            }}
            
            const script = document.createElement('script');
            script.src = 'https://challenges.cloudflare.com/turnstile/v0/api.js?onload=onTurnstileReady&render=explicit';
            script.async = true;
            script.defer = true;
            
            script.onload = () => {{
                console.log('Turnstile script loaded successfully');
                loading.textContent = 'CAPTCHA ready - please click to verify';
            }};
            
            script.onerror = () => {{
                console.error('Failed to load Turnstile script');
                loading.textContent = 'Failed to load CAPTCHA. Please check your connection.';
                loading.style.color = 'red';
            }};
            
            window.onTurnstileReady = function() {{
                console.log('🚀 Turnstile API ready');
                loading.textContent = 'Click the checkbox below to verify';
                
                // Try explicit rendering after delay
                setTimeout(() => {{
                    if (window.turnstile && window.turnstile.render) {{
                        try {{
                            window.turnstile.render('.cf-turnstile', {{
                                sitekey: '{websiteKey}',
                                callback: 'onCaptchaSuccess',
                                'error-callback': 'onCaptchaError',
                                'expired-callback': 'onCaptchaExpired',
                                action: '{action}',
                                theme: 'light',
                                size: 'normal'
                            }});
                            console.log('Explicit rendering successful');
                            loading.textContent = 'Please complete the verification';
                        }} catch (e) {{
                            console.log('Explicit render failed, using automatic rendering:', e);
                        }}
                    }}
                }}, 2000);
            }};
            
            document.head.appendChild(script);
            console.log('Turnstile script added to page');
        }};
        
        loadTurnstileScript();
        
        // Return success indicator
        return {{
            widget_created: true,
            callback_set: typeof window.onCaptchaSuccess === 'function',
            script_loading: true,
            overlay_id: 'captcha-overlay'
        }};
        """

        try:
            result = await page.evaluate(script)
            logger.debug(f"Captcha script executed: {result}")
            
            # Wait for widget to load
            await asyncio.sleep(3)
            
            # Verify widget is visible
            try:
                widget_visible = await page.locator('.cf-turnstile').is_visible(timeout=5000)
                logger.debug(f"Turnstile widget visible: {widget_visible}")
            except:
                logger.warning("Could not verify widget visibility")
                
        except Exception as e:
            logger.warning(f"Error loading captcha: {e}")
            # Try fallback method
            await self._load_captcha_fallback(page, websiteKey, action)

    async def _load_captcha_fallback(self, page, websiteKey: str, action: str = ''):
        """Fallback method for loading captcha"""
        try:
            logger.info("Using fallback captcha loading method")
            
            await page.evaluate(f"""
                // Simple widget insertion
                const div = document.createElement('div');
                div.className = 'cf-turnstile';
                div.setAttribute('data-sitekey', '{websiteKey}');
                div.setAttribute('data-callback', 'onCaptchaSuccess');
                div.style.cssText = `
                    position: fixed; top: 50%; left: 50%; transform: translate(-50%, -50%);
                    z-index: 9999; background: white; padding: 20px; border-radius: 8px;
                    box-shadow: 0 4px 20px rgba(0,0,0,0.3); cursor: pointer;
                `;
                document.body.appendChild(div);
                
                window.onCaptchaSuccess = function(token) {{
                    let input = document.createElement('input');
                    input.type = 'hidden';
                    input.name = 'cf-turnstile-response';
                    input.value = token;
                    document.body.appendChild(input);
                }};
                
                if (!document.querySelector('script[src*="turnstile"]')) {{
                    const script = document.createElement('script');
                    script.src = 'https://challenges.cloudflare.com/turnstile/v0/api.js';
                    script.async = true;
                    document.head.appendChild(script);
                }}
            """)
            
            await asyncio.sleep(3)
            logger.debug("Fallback captcha loaded")
            
        except Exception as e:
            logger.error(f"Fallback captcha loading failed: {e}")

    async def wait_for_turnstile_token(self, page, use_advanced_features=False) -> str | None:
        """Wait for turnstile token with cross-platform click logic"""
        try:
            locator = page.locator('input[name="cf-turnstile-response"]')
            token = ""
            start_time = time()
            timeout = 90
            last_click_time = 0
            click_strategies_tried = []
            
            logger.debug(f"Waiting for turnstile token (advanced features: {use_advanced_features})")
            
            while not token and (time() - start_time) < timeout:
                await asyncio.sleep(1)
                try:
                    token = await locator.input_value(timeout=1000)
                    
                    if not token:
                        current_time = time()
                        
                        # Try clicking every 5 seconds
                        if current_time - last_click_time > 5:
                            clicked = await self._try_click_strategies(page, click_strategies_tried, use_advanced_features)
                            if clicked:
                                last_click_time = current_time
                                
                except Exception as er:
                    logger.debug(f'Token check error: {er}')
                    
                if token:
                    elapsed = time() - start_time
                    logger.success(f'Got captcha token in {elapsed:.1f}s using strategies: {click_strategies_tried}')
                    break
                    
            if not token:
                logger.warning(f'Token not found after {timeout}s. Tried strategies: {click_strategies_tried}')
                
            return token
            
        except Exception as e:
            logger.error(f"Error waiting for token: {e}")
            return None

    async def _try_click_strategies(self, page, tried_strategies, use_advanced_features=False):
        """Cross-platform widget clicking with multiple strategies"""
        
        # Strategy 1: Direct element click
        selectors = [
            '.cf-turnstile',
            '[data-sitekey]',
            '.cf-turnstile iframe',
            'iframe[src*="turnstile"]',
            'iframe[src*="cloudflare"]',
            '[data-widget-id]',
            '.captcha-container'
        ]
        
        for selector in selectors:
            strategy_name = f"direct:{selector}"
            if strategy_name not in tried_strategies:
                try:
                    element = page.locator(selector).first
                    if await element.is_visible(timeout=2000):
                        await element.click(timeout=3000, force=True)
                        tried_strategies.append(strategy_name)
                        logger.debug(f'Direct click successful: {selector}')
                        return True
                except Exception as e:
                    logger.debug(f'Direct click failed for {selector}: {e}')
                    continue
        
        # Strategy 2: Iframe content click
        if "iframe_content" not in tried_strategies:
            try:
                iframes = page.locator('iframe')
                iframe_count = await iframes.count()
                for i in range(iframe_count):
                    iframe = iframes.nth(i)
                    src = await iframe.get_attribute('src')
                    if src and ('turnstile' in src or 'cloudflare' in src):
                        try:
                            iframe_content = await iframe.content_frame()
                            if iframe_content:
                                # Look for checkbox inside iframe
                                checkbox_selectors = [
                                    '[role="checkbox"]',
                                    'input[type="checkbox"]',
                                    '.cb-i',
                                    '[data-ray]'
                                ]
                                for cb_selector in checkbox_selectors:
                                    try:
                                        checkbox = iframe_content.locator(cb_selector)
                                        if await checkbox.is_visible(timeout=2000):
                                            await checkbox.click(timeout=3000)
                                            tried_strategies.append("iframe_content")
                                            logger.debug('Iframe content click successful')
                                            return True
                                    except:
                                        continue
                        except Exception as e:
                            logger.debug(f'Iframe content access failed: {e}')
                            continue
            except Exception as e:
                logger.debug(f'Iframe strategy failed: {e}')
        
        # Strategy 3: Center area click (blind click)
        if "center_click" not in tried_strategies:
            try:
                widget = page.locator('.cf-turnstile, [data-sitekey]').first
                if await widget.is_visible(timeout=2000):
                    box = await widget.bounding_box()
                    if box:
                        center_x = box['x'] + box['width'] / 2
                        center_y = box['y'] + box['height'] / 2
                        await page.mouse.click(center_x, center_y)
                        tried_strategies.append("center_click")
                        logger.debug('Center click successful')
                        return True
            except Exception as e:
                logger.debug(f'Center click failed: {e}')
        
        # Strategy 4: JavaScript trigger
        if "js_trigger" not in tried_strategies:
            try:
                await page.evaluate("""
                    // Try to trigger turnstile programmatically
                    if (window.turnstile && window.turnstile.execute) {
                        window.turnstile.execute();
                    }
                    
                    // Try clicking via JS
                    const widgets = document.querySelectorAll('.cf-turnstile, [data-sitekey]');
                    for (let widget of widgets) {
                        widget.click();
                        widget.dispatchEvent(new Event('click', {bubbles: true}));
                        widget.dispatchEvent(new Event('mousedown', {bubbles: true}));
                        widget.dispatchEvent(new Event('mouseup', {bubbles: true}));
                    }
                    
                    // Try checkbox clicks
                    const checkboxes = document.querySelectorAll('[role="checkbox"], input[type="checkbox"]');
                    for (let cb of checkboxes) {
                        cb.click();
                        cb.dispatchEvent(new Event('change', {bubbles: true}));
                    }
                """)
                tried_strategies.append("js_trigger")
                logger.debug('JavaScript trigger executed')
                return True
            except Exception as e:
                logger.debug(f'JavaScript trigger failed: {e}')
        
        # Strategy 5: Advanced click (computer vision)
        if use_advanced_features and "advanced_click" not in tried_strategies:
            try:
                if await self._advanced_click(page):
                    tried_strategies.append("advanced_click")
                    return True
            except Exception as e:
                logger.debug(f'Advanced click failed: {e}')
        
        # Strategy 6: Force interaction (hover + multiple clicks)
        if "force_interaction" not in tried_strategies and len(tried_strategies) > 3:
            try:
                widget = page.locator('.cf-turnstile, [data-sitekey], iframe').first
                if await widget.is_visible(timeout=2000):
                    # Hover first
                    await widget.hover(timeout=2000)
                    await asyncio.sleep(0.5)
                    
                    # Multiple click types
                    await widget.click(timeout=2000, force=True)
                    await widget.click(timeout=2000, button='right')
                    await widget.dblclick(timeout=2000)
                    
                    tried_strategies.append("force_interaction")
                    logger.debug('Force interaction successful')
                    return True
            except Exception as e:
                logger.debug(f'Force interaction failed: {e}')
        
        return False

    async def _advanced_click(self, page):
        """Advanced click using computer vision (cross-platform)"""
        if not cv2 or not np or not pyautogui:
            logger.debug("Computer vision libraries not available")
            return False
            
        try:
            # Take screenshot
            image_bytes = await page.screenshot(full_page=True)
            screen_np = np.frombuffer(image_bytes, dtype=np.uint8)
            screen = cv2.imdecode(screen_np, cv2.IMREAD_COLOR)

            # Try to find checkbox template
            template_paths = [
                "screens/checkbox.png",
                "templates/turnstile_checkbox.png",
                "assets/checkbox_template.png"
            ]
            
            for template_path in template_paths:
                if os.path.exists(template_path):
                    template = cv2.imread(template_path)
                    if template is not None:
                        result = cv2.matchTemplate(screen, template, cv2.TM_CCOEFF_NORMED)
                        _, max_val, _, max_loc = cv2.minMaxLoc(result)
                        
                        if max_val > 0.8:  # Lower threshold for better detection
                            h, w = template.shape[:2]
                            center_x = max_loc[0] + w // 2
                            center_y = max_loc[1] + h // 2
                            
                            # Calculate click coordinates
                            click_x, click_y = self._calculate_click_coords(page, center_x, center_y)
                            pyautogui.click(click_x, click_y)
                            logger.success(f"Advanced click successful with confidence {max_val:.2f}")
                            return True
            
            # Alternative: Look for common checkbox patterns
            gray = cv2.cvtColor(screen, cv2.COLOR_BGR2GRAY)
            
            # Find rectangular regions that might be checkboxes
            contours, _ = cv2.findContours(gray, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            for contour in contours:
                area = cv2.contourArea(contour)
                if 200 < area < 2000:  # Reasonable checkbox size
                    x, y, w, h = cv2.boundingRect(contour)
                    aspect_ratio = w / h
                    if 0.8 < aspect_ratio < 1.2:  # Square-ish shape
                        click_x, click_y = self._calculate_click_coords(page, x + w//2, y + h//2)
                        pyautogui.click(click_x, click_y)
                        logger.debug("Advanced click on detected rectangular region")
                        return True
                        
        except Exception as e:
            logger.debug(f"Advanced click failed: {e}")
            
        return False

    def _calculate_click_coords(self, page, x, y):
        """Calculate click coordinates cross-platform"""
        try:
            if hasattr(page, '_grid_position_id') and page._grid_position_id is not None:
                handler = BrowserHandler()
                if page._grid_position_id < len(handler.window_manager.grid):
                    pos = handler.window_manager.grid[page._grid_position_id]
                    screen_x = pos['x'] + x + random.randint(5, 10)
                    screen_y = pos['y'] + y + random.randint(75, 85)
                    return screen_x, screen_y
        except Exception as e:
            logger.debug(f"Failed to calculate window-relative coordinates: {e}")
        
        # Fallback to direct coordinates
        return x + random.randint(-5, 5), y + random.randint(-5, 5)

    async def block_rendering(self, page):
        """Block resources to speed up loading"""
        try:
            await page.route("**/*", self.route_handler)
            logger.debug("Resource blocking enabled")
        except Exception as e:
            logger.debug(f"Failed to enable resource blocking: {e}")

    async def unblock_rendering(self, page):
        """Unblock resources"""
        try:
            await page.unroute("**/*", self.route_handler)
            logger.debug("Resource blocking disabled")
        except Exception as e:
            logger.debug(f"Failed to disable resource blocking: {e}")

    async def route_handler(self, route):
        """Route handler for resource blocking"""
        blocked_extensions = ['.css', '.png', '.jpg', '.jpeg', '.gif', '.svg', '.woff', '.woff2', '.ttf', '.ico']
        url = route.request.url
        
        # Allow essential JavaScript and the page itself
        if any(url.endswith(ext) for ext in blocked_extensions):
            await route.abort()
        elif 'turnstile' in url or 'cloudflare' in url:
            await route.continue_()  # Always allow turnstile resources
        else:
            await route.continue_()

# Cross-platform testing and utility functions
async def test_cross_platform():
    """Test browser functionality cross-platform"""
    try:
        handler = BrowserHandler()
        system = platform.system()
        
        print(f"🧪 Testing CloudFlare Turnstile Solver on {system}")
        print(f"📊 Screen size: {handler.window_manager.screen_width}x{handler.window_manager.screen_height}")
        print(f"🖥️  Headless mode: {handler.headless}")
        print(f"🔧 GUI features: {cv2 is not None and pyautogui is not None}")
        print(f"🌐 Proxy support: {Proxy is not None}")
        print(f"📈 Process monitoring: {psutil is not None}")
        
        page = await handler.get_page()
        
        # Test basic navigation
        await page.goto("https://example.com", timeout=15000)
        title = await page.title()
        print(f"✅ Basic navigation successful: {title}")
        
        # Test JavaScript execution
        result = await page.evaluate("() => { return 'JavaScript works!'; }")
        print(f"✅ JavaScript execution: {result}")
        
        # Test screenshot capability
        screenshot = await page.screenshot()
        print(f"✅ Screenshot capability: {len(screenshot)} bytes")
        
        await handler.close_page(page)
        await handler.cleanup_all()
        
        print(f"🎉 All tests passed on {system}!")
        
    except Exception as e:
        print(f"❌ Test failed: {e}")
        import traceback
        traceback.print_exc()

async def test_captcha_solving():
    """Test actual captcha solving"""
    try:
        from models import CaptchaTask
        
        # Create test task
        task = CaptchaTask(
            id="test-123",
            type="AntiTurnstileTaskProxyLess",
            websiteURL="https://demo.turnstile.workers.dev",
            websiteKey="0x4AAAAAAA0SGzxWuGl6kriB"
        )
        
        print("🧪 Testing captcha solving...")
        browser = Browser()
        
        start_time = time()
        token = await browser.solve_captcha(task)
        elapsed = time() - start_time
        
        if token:
            print(f"🎉 Captcha solved successfully in {elapsed:.1f}s!")
            print(f"📝 Token: {token[:50]}...")
            return True
        else:
            print(f"❌ Captcha solving failed after {elapsed:.1f}s")
            return False
            
    except Exception as e:
        print(f"❌ Captcha test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

def get_system_info():
    """Get cross-platform system information"""
    info = {
        "platform": platform.system(),
        "platform_release": platform.release(),
        "platform_version": platform.version(),
        "architecture": platform.machine(),
        "processor": platform.processor(),
        "python_version": platform.python_version(),
    }
    
    # Add display info
    if platform.system() == "Linux":
        info["display"] = os.environ.get('DISPLAY', 'None')
    elif platform.system() == "Windows":
        info["display"] = "Available" if pyautogui else "Not available"
    
    # Add GUI capabilities
    info["pyautogui_available"] = pyautogui is not None
    info["opencv_available"] = cv2 is not None
    info["psutil_available"] = psutil is not None
    
    # Add browser info
    info["headless_forced"] = os.environ.get('FORCE_HEADLESS', '').lower() == 'true'
    
    return info

if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1:
        if sys.argv[1] == "test":
            asyncio.run(test_cross_platform())
        elif sys.argv[1] == "captcha":
            asyncio.run(test_captcha_solving())
        elif sys.argv[1] == "info":
            info = get_system_info()
            print("🖥️  System Information:")
            for key, value in info.items():
                print(f"   {key}: {value}")
    else:
        print("🔧 CloudFlare Turnstile Solver - Cross-Platform Browser Module")
        print("")
        print("Usage:")
        print("  python browser.py test     # Test browser functionality")
        print("  python browser.py captcha  # Test captcha solving")
        print("  python browser.py info     # Show system information")
        print("")
        print("🌐 Cross-platform support:")
        print("  ✅ Windows (GUI + Headless)")
        print("  ✅ Linux (GUI + Headless)")
        print("  ✅ macOS (Headless)")
        print("  ✅ Docker (Headless)")
        print("")
        asyncio.run(test_cross_platform())