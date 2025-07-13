import asyncio
import uuid
import json
from time import time
import os
import sys
import subprocess

from quart import Quart, request, jsonify # type: ignore
from loguru import logger
from dotenv import load_dotenv

# Add current directory to path to import existing modules
sys.path.append('/root/Desktop/cloudflare-testing')

try:
    from models import CaptchaTask, CaptchaTaskResponse, CaptchaCreateTaskPayload, CaptchaGetTaskPayload
    from app_tasker import Tasker as AppTasker
    from async_tasker import Tasker as AsyncTasker
except ImportError as e:
    logger.error(f"Failed to import modules: {e}")
    logger.info("Make sure you're in the correct directory with all project files")
    sys.exit(1)

load_dotenv()

class DockerTurnstileAPI:
    def __init__(self, max_workers: int = 3):
        self.app = Quart(__name__)
        self.max_workers = max_workers
        
        # Use existing project 2 components
        self.app_tasker = AppTasker()
        self.async_tasker = AsyncTasker(max_workers=max_workers, callback_fn=self.app_tasker.add_result)
        
        # Set solver in app_tasker
        self.app_tasker.solvers['AntiTurnstileTaskProxyLess'] = self.async_tasker
        
        self._setup_routes()
        
    def _setup_routes(self):
        """Setup API routes compatible with both formats"""
        self.app.before_serving(self._startup)
        self.app.after_serving(self._shutdown)
        
        # Original project 2 format
        self.app.route('/createTask', methods=['POST'])(self.create_task)
        self.app.route('/getTaskResult', methods=['POST'])(self.get_task_result)
        
        # Simple GET format for external use
        self.app.route('/turnstile', methods=['GET'])(self.turnstile_simple)
        self.app.route('/result', methods=['GET'])(self.get_result_simple)
        
        # Status and documentation
        self.app.route('/')(self.index)
        self.app.route('/status')(self.status)
        
    async def _startup(self):
        """Initialize on startup"""
        logger.info("🚀 Starting Docker Turnstile API...")
        logger.info(f"📊 Max workers: {self.max_workers}")
        
        # Check if we're in the right environment
        if not os.path.exists('/root/Desktop'):
            logger.warning("⚠️  Not running in expected Docker environment")
            
        # Set display for GUI components if needed
        if not os.environ.get('DISPLAY'):
            os.environ['DISPLAY'] = ':10.0'  # XRDP display
            logger.info("🖥️  Set DISPLAY to :10.0")
            
    async def _shutdown(self):
        """Cleanup on shutdown"""
        logger.info("🛑 Shutting down API...")
        
    async def create_task(self):
        """Handle /createTask endpoint (original project 2 format)"""
        try:
            data = await request.get_json()
            logger.info(f"📝 Received createTask: {data}")
            
            # Use existing app_tasker logic
            response = self.app_tasker.add_task(data)
            
            if response.taskId:
                # Start async solving
                task = self.app_tasker.tasks[response.taskId]['task']
                await self.async_tasker.add_task(task)
                logger.success(f"✅ Task {response.taskId} created and started")
            
            return jsonify(response.json()), 200
            
        except Exception as e:
            logger.error(f"❌ Error in createTask: {e}")
            return jsonify({
                "status": "error",
                "errorId": 1,
                "errorDescription": str(e)
            }), 500
            
    async def get_task_result(self):
        """Handle /getTaskResult endpoint (original project 2 format)"""
        try:
            data = await request.get_json()
            logger.debug(f"📋 Task result requested: {data}")
            
            # Use existing app_tasker logic
            response = self.app_tasker.get_result(data)
            
            # Log result with truncated token for security
            data = response.json()
            if data.get('status') == 'ready' and 'solution' in data:
                token = data['solution']['token']
                data_log = data.copy()
                data_log['solution']['token'] = token[:50] + '...'
                logger.info(f"📤 Returning result: {data_log}")
            else:
                logger.debug(f"📤 Returning result: {data}")
                
            return jsonify(response.json()), 200
            
        except Exception as e:
            logger.error(f"❌ Error in getTaskResult: {e}")
            return jsonify({
                "status": "error",
                "errorId": 1,
                "errorDescription": str(e)
            }), 500
            
    async def turnstile_simple(self):
        """Simple GET endpoint for external compatibility"""
        try:
            url = request.args.get('url')
            sitekey = request.args.get('sitekey')
            action = request.args.get('action', '')
            
            if not url or not sitekey:
                return jsonify({
                    "error": "Missing required parameters: url, sitekey"
                }), 400
                
            logger.info(f"🎯 Simple request: {url} | {sitekey}")
            
            # Create task using existing format
            task_data = {
                "clientKey": os.getenv('API_KEY', 'default_key'),
                "task": {
                    "type": "AntiTurnstileTaskProxyLess",
                    "websiteURL": url,
                    "websiteKey": sitekey
                }
            }
            
            response = self.app_tasker.add_task(task_data)
            
            if response.taskId:
                task = self.app_tasker.tasks[response.taskId]['task']
                await self.async_tasker.add_task(task)
                
                return jsonify({
                    "task_id": response.taskId,
                    "status": "created"
                }), 202
            else:
                return jsonify({
                    "error": response.errorDescription
                }), 400
                
        except Exception as e:
            logger.error(f"❌ Error in turnstile_simple: {e}")
            return jsonify({"error": str(e)}), 500
            
    async def get_result_simple(self):
        """Simple GET endpoint for result retrieval"""
        try:
            task_id = request.args.get('id')
            
            if not task_id:
                return jsonify({"error": "Missing task id parameter"}), 400
                
            # Use existing app_tasker logic
            get_data = {
                "clientKey": os.getenv('API_KEY', 'default_key'),
                "taskId": task_id
            }
            
            response = self.app_tasker.get_result(get_data)
            data = response.json()
            
            if data['status'] == 'ready':
                return jsonify({
                    "status": "ready",
                    "value": data['solution']['token']
                }), 200
            elif data['status'] == 'processing':
                return jsonify({
                    "status": "processing"
                }), 200
            elif data['status'] == 'error':
                return jsonify({
                    "status": "error",
                    "error": data.get('errorDescription', 'Unknown error')
                }), 422
            else:
                return jsonify({
                    "status": "unknown",
                    "data": data
                }), 200
                
        except Exception as e:
            logger.error(f"❌ Error in get_result_simple: {e}")
            return jsonify({"error": str(e)}), 500
            
    async def status(self):
        """API status endpoint"""
        return jsonify({
            "status": "online",
            "workers": self.max_workers,
            "active_tasks": len(self.app_tasker.tasks),
            "completed_results": len(self.app_tasker.results),
            "environment": "docker",
            "display": os.environ.get('DISPLAY', 'not_set')
        }), 200
        
    async def index(self):
        """API documentation homepage"""
        active_tasks = len(self.app_tasker.tasks)
        completed_results = len(self.app_tasker.results)
        
        return f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>Docker Turnstile Solver API</title>
            <style>
                body {{ font-family: Arial, sans-serif; margin: 40px; background: #f5f5f5; }}
                .container {{ background: white; padding: 30px; border-radius: 10px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }}
                .status {{ background: #d4edda; padding: 15px; border-radius: 5px; margin-bottom: 20px; }}
                .endpoint {{ background: #f8f9fa; padding: 15px; margin: 10px 0; border-radius: 5px; }}
                .method-post {{ color: #dc3545; font-weight: bold; }}
                .method-get {{ color: #28a745; font-weight: bold; }}
                code {{ background: #e9ecef; padding: 2px 6px; border-radius: 3px; font-size: 12px; }}
                .stats {{ display: flex; gap: 20px; }}
                .stat {{ background: #007bff; color: white; padding: 10px; border-radius: 5px; text-align: center; }}
            </style>
        </head>
        <body>
            <div class="container">
                <h1>🐳 Docker Turnstile Solver API</h1>
                
                <div class="status">
                    <strong>🟢 Status: Online</strong> | 
                    Workers: {self.max_workers} | 
                    Display: {os.environ.get('DISPLAY', 'not_set')}
                </div>
                
                <div class="stats">
                    <div class="stat">
                        <div>Active Tasks</div>
                        <div style="font-size: 24px;">{active_tasks}</div>
                    </div>
                    <div class="stat">
                        <div>Completed</div>
                        <div style="font-size: 24px;">{completed_results}</div>
                    </div>
                </div>
                
                <h2>📡 API Endpoints</h2>
                
                <h3>Original Format (Project 2 Compatible)</h3>
                <div class="endpoint">
                    <h4><span class="method-post">POST</span> /createTask</h4>
                    <p>Create captcha solving task</p>
                    <code>{{"clientKey": "api_key", "task": {{"type": "AntiTurnstileTaskProxyLess", "websiteURL": "https://example.com", "websiteKey": "0x4AAA..."}}}}</code>
                </div>
                
                <div class="endpoint">
                    <h4><span class="method-post">POST</span> /getTaskResult</h4>
                    <p>Get task result</p>
                    <code>{{"clientKey": "api_key", "taskId": "uuid"}}</code>
                </div>
                
                <h3>Simple Format (External Use)</h3>
                <div class="endpoint">
                    <h4><span class="method-get">GET</span> /turnstile</h4>
                    <p>Create task with URL parameters</p>
                    <code>/turnstile?url=https://example.com&sitekey=0x4AAA...</code>
                </div>
                
                <div class="endpoint">
                    <h4><span class="method-get">GET</span> /result</h4>
                    <p>Get result with task ID</p>
                    <code>/result?id=task_uuid</code>
                </div>
                
                <div class="endpoint">
                    <h4><span class="method-get">GET</span> /status</h4>
                    <p>API status and statistics</p>
                    <code>/status</code>
                </div>
                
                <p><small>🚀 Powered by original Project 2 logic running in Docker environment</small></p>
            </div>
        </body>
        </html>
        """

def main():
    import argparse
    
    parser = argparse.ArgumentParser(description="Docker Turnstile API Wrapper")
    parser.add_argument('--workers', type=int, default=3, help='Max workers')
    parser.add_argument('--host', type=str, default='0.0.0.0', help='Host')
    parser.add_argument('--port', type=int, default=5033, help='Port')
    parser.add_argument('--api-key', type=str, help='API key (overrides .env)')
    args = parser.parse_args()
    
    # Set API key if provided
    if args.api_key:
        os.environ['API_KEY'] = args.api_key
    elif not os.environ.get('API_KEY'):
        os.environ['API_KEY'] = 'default_docker_key_123'
        logger.warning("⚠️  Using default API key. Set API_KEY environment variable for production!")
    
    # Create API wrapper
    api = DockerTurnstileAPI(max_workers=args.workers)
    
    logger.info(f"🚀 Starting Docker Turnstile API")
    logger.info(f"🌐 Host: {args.host}:{args.port}")
    logger.info(f"👥 Workers: {args.workers}")
    logger.info(f"🔑 API Key: {os.environ.get('API_KEY', 'not_set')}")
    logger.info(f"🖥️  Display: {os.environ.get('DISPLAY', 'not_set')}")
    
    # Run the app
    api.app.run(host=args.host, port=args.port, debug=False)

if __name__ == '__main__':
    main()
