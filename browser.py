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

# Only import psutil for process management
try:
    import psutil
except ImportError:
    psutil = None

# Cross-platform GUI dependencies
try:
    if platform.system() == "Windows":
        import cv2
        import numpy as np
        import pyautogui
        try:
            import win32gui
            import win32con
            import win32api
        except ImportError:
            win32gui = None
            win32con = None
            win32api = None
    elif platform.system() == "Linux":
        import cv2
        import numpy as np
        if os.environ.get('DISPLAY'):
            import pyautogui
        else:
            pyautogui = None
        win32gui = None
        win32con = None
        win32api = None
    else:
        cv2 = None
        np = None
        pyautogui = None
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

class CrossPlatformWindowGridManager:
    """Cross-platform window grid manager for Windows and Linux"""
    
    def __init__(self, window_width=500, window_height=200, vertical_overlap=60):
        self.window_width = window_width
        self.window_height = window_height
        self.vertical_step = window_height - vertical_overlap
        self.system = platform.system()

        screen_width, screen_height = self.get_screen_size()
        self.cols = max(1, screen_width // window_width)
        self.rows = max(1, screen_height // self.vertical_step)

        self.grid = []
        self._generate_grid()

    def get_screen_size(self):
        """Get screen size for both Windows and Linux"""
        try:
            if self.system == "Windows":
                return self._get_windows_screen_size()
            elif self.system == "Linux":
                return self._get_linux_screen_size()
            else:
                return self._get_fallback_screen_size()
                
        except Exception as e:
            logger.error(f"Error getting screen size: {e}")
            return 1920, 1080

    def _get_windows_screen_size(self):
        """Get screen size on Windows"""
        try:
            # Method 1: Using win32api (most accurate)
            if win32api and win32con:
                width = win32api.GetSystemMetrics(win32con.SM_CXSCREEN)
                height = win32api.GetSystemMetrics(win32con.SM_CYSCREEN)
                logger.debug(f"Windows screen size from win32api: {width}x{height}")
                return width, height
        except Exception as e:
            logger.debug(f"win32api failed: {e}")
        
        try:
            # Method 2: Using tkinter (fallback)
            import tkinter as tk
            root = tk.Tk()
            width = root.winfo_screenwidth()
            height = root.winfo_screenheight()
            root.destroy()
            logger.debug(f"Windows screen size from tkinter: {width}x{height}")
            return width, height
        except Exception as e:
            logger.debug(f"tkinter failed: {e}")
        
        try:
            # Method 3: Using pyautogui (if available)
            if pyautogui:
                size = pyautogui.size()
                logger.debug(f"Windows screen size from pyautogui: {size.width}x{size.height}")
                return size.width, size.height
        except Exception as e:
            logger.debug(f"pyautogui failed: {e}")
        
        # Method 4: Environment variables
        if os.environ.get('SCREEN_WIDTH') and os.environ.get('SCREEN_HEIGHT'):
            width = int(os.environ.get('SCREEN_WIDTH'))
            height = int(os.environ.get('SCREEN_HEIGHT'))
            logger.debug(f"Windows screen size from env: {width}x{height}")
            return width, height
        
        # Fallback for Windows
        logger.warning("Could not detect Windows screen size, using default 1920x1080")
        return 1920, 1080

    def _get_linux_screen_size(self):
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

    def _get_fallback_screen_size(self):
        """Fallback screen size detection for other platforms"""
        try:
            # Try tkinter first
            import tkinter as tk
            root = tk.Tk()
            width = root.winfo_screenwidth()
            height = root.winfo_screenheight()
            root.destroy()
            logger.debug(f"Fallback screen size from tkinter: {width}x{height}")
            return width, height
        except Exception as e:
            logger.debug(f"Fallback tkinter failed: {e}")
        
        # Use environment variables or default
        width = int(os.environ.get('SCREEN_WIDTH', 1920))
        height = int(os.environ.get('SCREEN_HEIGHT', 1080))
        logger.warning(f"Using fallback screen size: {width}x{height}")
        return width, height

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
        self.window_manager = CrossPlatformWindowGridManager()
        self.headless = self._should_run_headless()
        self.system = platform.system()
        self.proxy_config = {
            "api_key": os.getenv('KIOT_PROXY_KEY'),
            "region": os.getenv('PROXY_REGION', 'random'),
            "proxy": None,
            "ttc": 59,
            "last_fetch": 0,
            "lock": asyncio.Lock()
        }
        self.proxy_task = None
        self.browser_processes = set()
        self.last_cleanup = time()

    @staticmethod
    def read_proxy():
        """Read proxy configuration from environment"""
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

    def _should_run_headless(self):
        """Determine if should run headless based on environment and platform"""
        
        # Force headless if explicitly set
        if os.environ.get('FORCE_HEADLESS', '').lower() == 'true':
            return True
        
        system = platform.system()
        
        if system == "Windows":
            return self._should_run_headless_windows()
        elif system == "Linux":
            return self._should_run_headless_linux()
        else:
            logger.info("Unknown platform, running headless")
            return True

    def _should_run_headless_windows(self):
        """Windows-specific headless detection"""
        # Check if running as Windows service or in background
        try:
            if win32gui:
                # If we can't get foreground window, probably running as service
                if not win32gui.GetForegroundWindow():
                    logger.info("Running as Windows service, using headless mode")
                    return True
        except:
            pass
        
        # Check if pyautogui is available
        if not pyautogui:
            logger.info("pyautogui not available on Windows, running headless")
            return True
        
        # Check if we're in a terminal without GUI
        if not os.environ.get('SESSIONNAME'):
            logger.info("No Windows session detected, running headless")
            return True
        
        logger.info("Windows GUI environment detected, running with display")
        return False

    def _should_run_headless_linux(self):
        """Linux-specific headless detection"""
        # Run headless if no display available
        if not os.environ.get('DISPLAY'):
            logger.info("No DISPLAY environment variable, running headless")
            return True
            
        # Run headless if pyautogui not available
        if not pyautogui:
            logger.info("pyautogui not available, running headless")
            return True
            
        # Check if we can actually use the display
        try:
            import subprocess
            result = subprocess.run(['xdpyinfo'], capture_output=True, timeout=5)
            if result.returncode != 0:
                logger.info("Cannot access X display, running headless")
                return True
        except Exception:
            logger.info("xdpyinfo not available, running headless")
            return True
            
        logger.info("Linux display available, running with GUI")
        return False
        
    def cleanup_zombie_processes(self):
        """Kill zombie browser processes (cross-platform)"""
        if not psutil:
            return
            
        try:
            current_time = time()
            if current_time - self.last_cleanup < 60:  # Cleanup every minute
                return
            
            process_names = ['chrome', 'chromium', 'chromium-browser']
            if self.system == "Windows":
                process_names.extend(['chrome.exe', 'chromium.exe', 'msedge.exe'])
                
            for proc in psutil.process_iter(['pid', 'name', 'create_time']):
                try:
                    if proc.info['name'] == process_names:
                        # Kill processes older than 5 minutes
                        if current_time - proc.info['create_time'] > 300:
                            logger.warning(f"Killing zombie browser process {proc.info['pid']} ({proc.info['name']})")
                            proc.kill()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
                    
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
                            logger.success(
                                f"Loaded proxy: {proxy_data['http']} | "
                                f"Location: {proxy_data['location']}"
 | f"TTC: {proxy_data['ttc']}s"
                            )
                        else:
                            error_msg = data.get('message', 'Unknown error')
                            logger.error(f"KiotProxy API error: {error_msg}")
                            if "limit" in error_msg.lower() or "rate" in error_msg.lower():
                                logger.warning("Rate limit detected, waiting 60s...")
                                await asyncio.sleep(60)
                            config["proxy"] = None
                            raise ValueError(error_msg)
            except aiohttp.ClientError as e:
                logger.error(f"Network error: {str(e)}")
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
                logger.debug(f"Next refresh in {wait_time}s")
                await asyncio.sleep(wait_time)

            except Exception as e:
                consecutive_errors += 1

                if "limit" in str(e).lower() or "rate" in str(e).lower():
                    wait_time = min(300 * consecutive_errors, 1800)
                    logger.error(f"Rate limit #{consecutive_errors}, waiting {wait_time}s")
                else:
                    wait_time = min(60 * consecutive_errors, 600)
                    logger.error(f"Error #{consecutive_errors}: {str(e)}, waiting {wait_time}s")

                await asyncio.sleep(wait_time)

    async def launch(self):
        """Launch browser with cross-platform support"""
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
                '--disable-images',
                '--memory-pressure-off',
                '--max_old_space_size=512',
            ]
            
            # Platform-specific arguments
            if self.system == "Windows":
                args.extend([
                    '--disable-gpu-sandbox',
                    '--disable-software-rasterizer',
                ])
            
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
                'timeout': 30000,
            }
            
            if self.proxy:
                launch_options['proxy'] = self.proxy
                
            # Launch browser
            try:
                self.browser = await asyncio.wait_for(
                    self.playwright.chromium.launch(**launch_options),
                    timeout=30
                )
                logger.success(f"Browser launched successfully on {self.system}")
            except asyncio.TimeoutError:
                logger.error("Browser launch timeout")
                raise
                
        except Exception as e:
            logger.error(f"Failed to launch browser on {self.system}: {e}")
            await self.cleanup_all()
            raise

    async def get_page(self):
        """Get page with cross-platform support"""
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
                    context_options['viewport'] = {"width": 500, "height": 100}
                else:
                    context_options['viewport'] = {"width": 1920, "height": 1080}
                    
                context = await self.browser.new_context(**context_options)
                
                # Set timeout AFTER context creation
                context.set_default_timeout(30000)
                context.set_default_navigation_timeout(30000)

                page = await context.new_page()
                
                # Set window position for non-headless
                if not self.headless:
                    try:
                        position = self.window_manager.get_free_position()
                        await self.set_window_position(page, position["x"], position["y"])
                        page._grid_position_id = position["id"]
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

    async def set_window_position(self, page, x, y):
        """Set window position (cross-platform)"""
        if self.headless:
            return
            
        try:
            if self.system == "Windows":
                await self._set_window_position_windows(page, x, y)
            else:
                await self._set_window_position_linux(page, x, y)
        except Exception as e:
            logger.debug(f"Failed to set window position: {e}")

    async def _set_window_position_windows(self, page, x, y):
        """Set window position on Windows"""
        try:
            # Method 1: Chrome DevTools Protocol
            session = await page.context.new_cdp_session(page)
            result = await session.send("Browser.getWindowForTarget")
            window_id = result["windowId"]
            await session.send("Browser.setWindowBounds", {
                "windowId": window_id,
                "bounds": {
                    "left": x,
                    "top": y,
                    "width": 500,
                    "height": 200
                }
            })
            logger.debug(f"Windows window positioned at {x},{y}")
        except Exception as e:
            logger.debug(f"Windows CDP positioning failed: {e}")
            
            # Method 2: Try win32gui if available
            if win32gui and win32con:
                try:
                    await asyncio.sleep(0.5)  # Wait for window to appear
                    
                    def enum_windows_callback(hwnd, windows):
                        if win32gui.IsWindowVisible(hwnd):
                            window_text = win32gui.GetWindowText(hwnd)
                            if "Chrome" in window_text or "Chromium" in window_text:
                                windows.append(hwnd)
                    
                    windows = []
                    win32gui.EnumWindows(enum_windows_callback, windows)
                    
                    if windows:
                        hwnd = windows[-1]  # Get the latest window
                        win32gui.SetWindowPos(hwnd, win32con.HWND_TOP, x, y, 500, 200, win32con.SWP_SHOWWINDOW)
                        logger.debug(f"Windows window positioned via win32gui at {x},{y}")
                except Exception as e2:
                    logger.debug(f"win32gui positioning failed: {e2}")

    async def _set_window_position_linux(self, page, x, y):
        """Set window position on Linux"""
        try:
            session = await page.context.new_cdp_session(page)
            result = await session.send("Browser.getWindowForTarget")
            window_id = result["windowId"]
            await session.send("Browser.setWindowBounds", {
                "windowId": window_id,
                "bounds": {
                    "left": x,
                    "top": y,
                    "width": 500,
                    "height": 200
                }
            })
            logger.debug(f"Linux window positioned at {x},{y}")
        except Exception as e:
            logger.debug(f"Linux CDP positioning failed: {e}")

    async def cleanup_all(self):
        """Complete cleanup of all browser resources"""
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
        """Cross-platform captcha solving"""
        page = None
        try:
            async def _solve():
                nonlocal page
                page = await BrowserHandler().get_page()
                
                # Determine if we can use advanced features
                handler = BrowserHandler()
                use_advanced_features = not handler.headless and cv2 and pyautogui
                
                if use_advanced_features:
                    await self.block_rendering(page)
                    
                logger.debug(f"Navigating to {task.websiteURL}")
                await page.goto(task.websiteURL, timeout=30000)

                
                if use_advanced_features:
                    await self.unblock_rendering(page)
                    
                await self.load_captcha(page, websiteKey=task.websiteKey)
                await page.reload(timeout=30000)  # Thêm refresh page ngay sau khi mở URL
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
        """Load captcha with improved script and better widget setup"""
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
        captchaDiv.setAttribute('data-theme', 'light');
        captchaDiv.setAttribute('data-size', 'normal');
        captchaDiv.style.cursor = 'pointer';

        overlay.appendChild(captchaDiv);
        document.body.appendChild(overlay);

        // Global callback function
        window.onCaptchaSuccess = function(token) {{
            console.log('Captcha solved successfully!');
            
            // Store token in a hidden input for easy retrieval
            let tokenInput = document.querySelector('input[name="cf-turnstile-response"]');
            if (!tokenInput) {{
                tokenInput = document.createElement('input');
                tokenInput.type = 'hidden';
                tokenInput.name = 'cf-turnstile-response';
                document.body.appendChild(tokenInput);
            }}
            tokenInput.value = token;
        }};

        // Load Cloudflare Turnstile script if not already loaded
        if (!document.querySelector('script[src*="turnstile"]') && !window.turnstile) {{
            const script = document.createElement('script');
            script.src = 'https://challenges.cloudflare.com/turnstile/v0/api.js?onload=onTurnstileLoad';
            script.async = true;
            script.defer = true;
            
            // Add onload callback
            window.onTurnstileLoad = function() {{
                console.log('Turnstile script loaded');
                // Try to render if turnstile API is available
                if (window.turnstile && window.turnstile.render) {{
                    try {{
                        window.turnstile.render('.cf-turnstile', {{
                            sitekey: '{websiteKey}',
                            callback: 'onCaptchaSuccess',
                            action: '{action}',
                            theme: 'light',
                            size: 'normal'
                        }});
                        console.log('Immediate render successful');
                    }} catch (e) {{
                        console.log('Immediate render failed:', e);
                    }}
                }}, 1000);
            }}
        }}
        
        // Return success
        return 'Captcha widget setup completed';
        """

        try:
            result = await page.evaluate(script)
            logger.debug(f"Captcha script executed: {result}")
            
            # Wait a bit for the widget to load
            await asyncio.sleep(2)
            
            # Check if widget is visible
            widget_visible = await page.locator('.cf-turnstile').is_visible(timeout=5000)
            logger.debug(f"Turnstile widget visible: {widget_visible}")
            
        except Exception as e:
            logger.warning(f"Error loading captcha: {e}")
            # Try fallback method
            await self._load_captcha_fallback(page, websiteKey, action)

    async def _load_captcha_fallback(self, page, websiteKey: str, action: str = ''):
        """Fallback method for loading captcha"""
        try:
            logger.info("Using fallback captcha loading method")
            
            # Simple widget insertion
            await page.evaluate(f"""
                const div = document.createElement('div');
                div.className = 'cf-turnstile';
                div.setAttribute('data-sitekey', '{websiteKey}');
                div.setAttribute('data-callback', 'onCaptchaSuccess');
                div.style.position = 'fixed';
                div.style.top = '50%';
                div.style.left = '50%';
                div.style.transform = 'translate(-50%, -50%)';
                div.style.zIndex = '9999';
                div.style.backgroundColor = 'white';
                div.style.padding = '20px';
                div.style.border = '2px solid #ccc';
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
        """Improved token waiting with better error handling and click logic"""
        try:
            locator = page.locator('input[name="cf-turnstile-response"]')

            token = ""
            start_time = time()
            timeout = 90  # 90 seconds timeout
            click_attempted = False
            last_click_time = 0
            
            while not token and (time() - start_time) < timeout:
                await asyncio.sleep(1)
                try:
                    token = await locator.input_value(timeout=1000)
                    
                    if not token:
                        current_time = time()
                        
                        # Try clicking the widget if not clicked recently
                        if current_time - last_click_time > 5:  # Wait 5 seconds between clicks
                            try:
                                # Try different selectors for the turnstile widget
                                selectors = [
                                    '.cf-turnstile',
                                    '[data-sitekey]',
                                    'iframe[src*="turnstile"]',
                                    '.cf-turnstile iframe',
                                    '#cf-chl-widget'
                                ]
                                
                                clicked = False
                                for selector in selectors:
                                    try:
                                        widget = page.locator(selector).first
                                        if await widget.is_visible(timeout=2000):
                                            await widget.click(timeout=2000)
                                            last_click_time = current_time
                                            clicked = True
                                            logger.debug(f'Widget clicked using selector: {selector}')
                                            break
                                    except:
                                        continue
                                
                                if not clicked and use_advanced_features:
                                    # Try advanced checkbox detection as fallback
                                    if await self.check_for_checkbox(page):
                                        last_click_time = current_time
                                        logger.debug('Advanced checkbox click performed')
                                elif not clicked:
                                    # Try iframe click as last resort
                                    try:
                                        iframes = page.locator('iframe')
                                        iframe_count = await iframes.count()
                                        for i in range(iframe_count):
                                            iframe = iframes.nth(i)
                                            src = await iframe.get_attribute('src')
                                            if src and 'turnstile' in src:
                                                await iframe.click(timeout=2000)
                                                last_click_time = current_time
                                                logger.debug('Iframe clicked')
                                                break
                                    except:
                                        pass
                                        
                            except Exception as click_error:
                                logger.debug(f'Click attempt failed: {click_error}')
                            
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

    async def check_for_checkbox(self, page):
        """Advanced checkbox detection using computer vision"""
        
        if not cv2 or not np or not pyautogui:
            logger.debug("CV libraries not available, skipping advanced detection")
            return False
            
        try:
            # Take screenshot
            image_bytes = await page.screenshot(full_page=True)
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
                x, y = self.get_coords_to_click(page, center_x, center_y)
                
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

    async def block_rendering(self, page):
        await page.route("**/*", self.route_handler)

    async def unblock_rendering(self, page):
        await page.unroute("**/*", self.route_handler)