# -*- coding: utf-8 -*-

import os
import time
import asyncio
import aiohttp
from loguru import logger
from typing import Optional
from patchright.async_api import async_playwright
from proxystr import Proxy
import cv2
import numpy as np
import pyautogui
import random
import ctypes

from source import Singleton
from models import CaptchaTask

from dotenv import load_dotenv
load_dotenv()

class WindowGridManager:
    def __init__(self, window_width=500, window_height=200, vertical_overlap=60):
        self.window_width = window_width
        self.window_height = window_height
        self.vertical_step = window_height - vertical_overlap

        screen_width, screen_height = self.get_screen_size()
        self.cols = screen_width // window_width
        self.rows = screen_height // self.vertical_step

        self.grid = []
        self._generate_grid()

    @staticmethod
    def get_screen_size():
        user32 = ctypes.windll.user32
        user32.SetProcessDPIAware()  # Để lấy kích thước chính xác trên màn hình DPI cao
        screen_width = user32.GetSystemMetrics(0)
        screen_height = user32.GetSystemMetrics(1)
        return screen_width, screen_height

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
        raise RuntimeError("Không còn vị trí trống cho cửa sổ.")

    def release_position(self, pos_id):
        for pos in self.grid:
            if pos["id"] == pos_id:
                pos["is_occupied"] = False
                return
        raise ValueError(f"Vị trí {pos_id} không tồn tại.")

    def reset(self):
        for pos in self.grid:
            pos["is_occupied"] = False

class BrowserHandler(metaclass=Singleton):
    def __init__(self):
        self.playwright = None
        self.browser = None
        self.window_manager = WindowGridManager()
        self.proxy_config = {
            "api_key": os.getenv('KIOT_PROXY_KEY'),
            "region": os.getenv('PROXY_REGION', 'random'),
            "proxy": None,
            "ttc": 59,
            "last_fetch": 0,
            "lock": asyncio.Lock()
        }
        self.proxy_task = None

    async def _load_kiotproxy(self):
        """Tải proxy mới từ KiotProxy API"""
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
                            config["last_fetch"] = time.time()
                            logger.success(
                                f"Đã tải proxy: {proxy_data['http']} | "
                                f"Vị trí: {proxy_data['location']} | "
                                f"TTC: {proxy_data['ttc']}s"
                            )
                        else:
                            error_msg = data.get('message', 'Lỗi không xác định')
                            logger.error(f"Lỗi API KiotProxy: {error_msg}")
                            if "giới hạn" in error_msg or "rate" in error_msg.lower():
                                logger.warning("Phát hiện giới hạn rate, chờ 60s...")
                                await asyncio.sleep(60)
                            config["proxy"] = None
                            raise ValueError(error_msg)
            except aiohttp.ClientError as e:
                logger.error(f"Lỗi mạng: {str(e)}")
                raise
            except Exception as e:
                logger.error(f"Lỗi tải proxy: {str(e)}")
                raise

    async def _refresh_proxy_periodically(self):
        """Làm mới proxy định kỳ"""
        config = self.proxy_config
        consecutive_errors = 0

        while True:
            try:
                # Kiểm tra proxy còn hợp lệ
                if config["proxy"] and config["ttc"]:
                    time_since_fetch = time.time() - config["last_fetch"]
                    time_remaining = config["ttc"] - time_since_fetch
                    if time_remaining > 60:
                        logger.debug(f"Proxy còn hợp lệ trong {time_remaining:.0f}s")
                        await asyncio.sleep(min(time_remaining - 30, 300))
                        continue

                # Tải proxy mới
                logger.info("Đang làm mới proxy...")
                await self._load_kiotproxy()
                consecutive_errors = 0

                # Chờ theo TTC
                wait_time = max(config["ttc"] - 30, 60) if config["ttc"] else 300
                logger.debug(f"Làm mới tiếp theo sau {wait_time}s")
                await asyncio.sleep(wait_time)

            except Exception as e:
                consecutive_errors += 1

                if "giới hạn" in str(e) or "rate" in str(e).lower():
                    wait_time = min(300 * consecutive_errors, 1800)
                    logger.error(f"Giới hạn rate #{consecutive_errors}, chờ {wait_time}s")
                else:
                    wait_time = min(60 * consecutive_errors, 600)
                    logger.error(f"Lỗi #{consecutive_errors}: {str(e)}, chờ {wait_time}s")

                await asyncio.sleep(wait_time)

    async def launch(self):
        """Khởi động browser Chrome và task proxy nếu chưa có"""
        if not self.proxy_task:
            self.proxy_task = asyncio.create_task(self._refresh_proxy_periodically())
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(
            channel='chrome',  # Sử dụng Chrome
            headless=False,
            args=[
                '--disable-blink-features=AutomationControlled',
                "--window-size=500,200",
                "--window-position=0,0"
            ]
        )

    async def get_page(self):
        if not self.playwright or not self.browser:
            await self.launch()

        config = self.proxy_config
        proxy_options = {}
        async with config["lock"]:
            if config["proxy"]:
                proxy_options["proxy"] = {"server": config["proxy"]}
                logger.debug(f"Sử dụng proxy: {config['proxy']}")

        context = await self.browser.new_context(viewport={"width": 500, "height": 100}, **proxy_options)
        page = await context.new_page()
        position = self.window_manager.get_free_position()
        await self.set_window_position(page, position["x"], position["y"])
        page._grid_position_id = position["id"]
        return page

    @staticmethod
    async def set_window_position(page, x, y):
        session = await page.context.new_cdp_session(page)
        # Lấy windowId
        result = await session.send("Browser.getWindowForTarget")
        window_id = result["windowId"]
        # Đặt vị trí
        await session.send("Browser.setWindowBounds", {
            "windowId": window_id,
            "bounds": {
                "left": x,
                "top": y,
                "width": 500,
                "height": 200
            }
        })

    async def close(self):
        """Đóng browser"""
        try:
            await self.browser.close()
        except Exception:
            pass
        try:
            await self.playwright.stop()
        except Exception:
            pass

    async def close_page(self, page):
        self.window_manager.release_position(page._grid_position_id)
        await page.close()
        await page.context.close()

class Browser:
    def __init__(self, page=None):
        self.page = page
        self.lock = asyncio.Lock()

    async def solve_captcha(self, task: CaptchaTask):
        if not self.page:
            self.page = await BrowserHandler().get_page()

        async with self.lock:
            try:
                await self.block_rendering()
                await self.page.goto(task.websiteURL)
                await self.unblock_rendering()
                await self.load_captcha(websiteKey=task.websiteKey)
                return await self.wait_for_turnstile_token()
            finally:
                await BrowserHandler().close_page(self.page)
                self.page = None

    async def load_captcha(self, websiteKey: str = '0x4AAAAAAA0SGzxWuGl6kriB', action: str = ''):
        script = f"""
        // Xóa captcha cũ nếu có
        const existing = document.querySelector('#captcha-overlay');
        if (existing) existing.remove();

        // Tạo overlay
        const overlay = document.createElement('div');
        overlay.id = 'captcha-overlay';
        overlay.style.position = 'absolute';
        overlay.style.top = '0';
        overlay.style.left = '0';
        overlay.style.width = '100vw';
        overlay.style.height = '100vh';
        overlay.style.backgroundColor = 'rgba(0, 0, 0, 0.5)';
        overlay.style.display = 'block';
        overlay.style.justifyContent = 'center';
        overlay.style.alignItems = 'center';
        overlay.style.zIndex = '1000';

        // Thêm div captcha
        const captchaDiv = document.createElement('div');
        captchaDiv.className = 'cf-turnstile';
        captchaDiv.setAttribute('data-sitekey', '{websiteKey}');
        captchaDiv.setAttribute('data-callback', 'onCaptchaSuccess');
        captchaDiv.setAttribute('data-action', '');

        overlay.appendChild(captchaDiv);
        document.body.appendChild(overlay);

        // Tải script Cloudflare Turnstile
        const script = document.createElement('script');
        script.src = 'https://challenges.cloudflare.com/turnstile/v0/api.js';
        script.async = true;
        script.defer = true;
        document.head.appendChild(script);
        """
        await self.page.evaluate(script)

    async def wait_for_turnstile_token(self) -> str | None:
        locator = self.page.locator('input[name="cf-turnstile-response"]')

        token = ""
        t = time.time()
        while not token:
            await asyncio.sleep(0.5)
            try:
                token = await locator.input_value(timeout=500)
                if await self.check_for_checkbox():
                    logger.debug('Nhấp checkbox')
            except Exception as er:
                logger.error(er)
                pass
            if token:
                logger.debug(f'Nhận token captcha: {token}')
            if t + 15 < time.time():
                logger.warning('Không tìm thấy token')
                return None
        return token

    @staticmethod
    def get_coords_to_click(page, x, y):
        id_ = page._grid_position_id
        pos = BrowserHandler().window_manager.grid[id_]
        _x, _y = pos['x'], pos['y']
        return _x + x + random.randint(5, 10), _y + y + random.randint(75, 85)

    async def check_for_checkbox(self):
        # Chụp màn hình
        image_bytes = await self.page.screenshot(full_page=True)

        # Xử lý với OpenCV
        screen_np = np.frombuffer(image_bytes, dtype=np.uint8)
        screen = cv2.imdecode(screen_np, cv2.IMREAD_COLOR)

        # Tải template checkbox
        template = cv2.imread("screens/checkbox.png")

        # Khớp template
        result = cv2.matchTemplate(screen, template, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(result)

        if max_val > 0.9:
            logger.debug(f"Tìm thấy checkbox! Độ chính xác: {max_val}")
            h, w = template.shape[:2]
            center_x = max_loc[0] + w // 2
            center_y = max_loc[1] + h // 2
            x, y = self.get_coords_to_click(self.page, center_x, center_y)
            pyautogui.click(x, y)
            return True

    async def human_like_mouse_move(self, start_x: int, start_y: int, end_x: int, end_y: int, steps: int = 25):
        """Di chuyển chuột giống con người"""
        await self.page.mouse.move(start_x, start_y)
        for i in range(1, steps + 1):
            progress = i / steps
            x_noise = random.uniform(-1, 1)
            y_noise = random.uniform(-1, 1)
            x = start_x + (end_x - start_x) * progress + x_noise
            y = start_y + (end_y - start_y) * progress + y_noise
            await self.page.mouse.move(x, y)
            await asyncio.sleep(random.uniform(0.005, 0.02))

    async def human_click(self, x: int, y: int):
        """Nhấp chuột giống con người"""
        try:
            await self.page.mouse.move(0, 0)
        except Exception:
            pass

        await self.human_like_mouse_move(0, 0, x, y, steps=random.randint(15, 30))
        await asyncio.sleep(random.uniform(0.05, 0.15))
        await self.page.mouse.down()
        await asyncio.sleep(random.uniform(0.05, 0.12))
        await self.page.mouse.up()
        if random.random() < 0.4:
            await self.page.mouse.move(x + random.randint(-3, 3), y + random.randint(-3, 3))

    async def route_handler(self, route):
        blocked_extensions = ['.js', '.css', '.png', '.jpg', '.svg', '.gif', '.woff', '.ttf']
        if any(route.request.url.endswith(ext) for ext in blocked_extensions):
            await route.abort()
        else:
            await route.continue_()

    async def block_rendering(self):
        await self.page.route("**/*", self.route_handler)

    async def unblock_rendering(self):
        await self.page.unroute("**/*", self.route_handler)
