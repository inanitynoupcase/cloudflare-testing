# api_server.py - Wrapper API for existing browser logic

import asyncio
import uuid
import json
from time import time
from typing import Dict, Any
import os

from quart import Quart, request, jsonify
from loguru import logger
from dotenv import load_dotenv

# Import existing components from project 2
from browser import Browser, BrowserHandler
from models import CaptchaTask, CaptchaTaskResponse, CaptchaCreateTaskPayload, CaptchaGetTaskPayload
from async_tasker import Tasker

load_dotenv()

class TurnstileAPIWrapper:
    def __init__(self, max_workers: int = 3):
        self.app = Quart(__name__)
        self.max_workers = max_workers
        
        # Task storage (in-memory for simplicity)
        self.tasks = {}
        self.results = {}
        
        # Initialize the async tasker from project 2
        self.solver = Tasker(max_workers=max_workers, callback_fn=self._task_completed)
        
        self._setup_routes()
        
    def _setup_routes(self):
        """Setup API routes"""
        self.app.before_serving(self._startup)
        self.app.after_serving(self._shutdown)
        
        # API endpoints matching project 2 format
        self.app.route('/createTask', methods=['POST'])(self.create_task)
        self.app.route('/getTaskResult', methods=['POST'])(self.get_task_result)
        
        # Additional endpoints for external API compatibility
        self.app.route('/turnstile', methods=['GET'])(self.turnstile_simple)
        self.app.route('/result', methods=['GET'])(self.get_result_simple)
        self.app.route('/')(self.index)
        
    async def _startup(self):
        """Initialize browser handler"""
        logger.info("Starting API server...")
        # Browser handler will be initialized on first use
        asyncio.create_task(self._cleanup_loop())
        
    async def _shutdown(self):
        """Cleanup on shutdown"""
        logger.info("Shutting down API server...")
        await BrowserHandler().close()
        
    async def _cleanup_loop(self):
        """Periodic cleanup of old tasks/results"""
        while True:
            try:
                now = time()
                
                # Clean expired tasks (2 minutes)
                for task_id in list(self.tasks.keys()):
                    if self.tasks[task_id]['created_at'] + 120 < now:
                        if task_id not in self.results:
                            # Task expired without completion
                            self.results[task_id] = {
                                'created_at': now,
                                'result': CaptchaTaskResponse(
                                    taskId=task_id,
                                    status='error',
                                    errorId=1,
                                    errorDescription='Task expired'
                                )
                            }
                        del self.tasks[task_id]
                        logger.debug(f"Cleaned expired task {task_id}")
                
                # Clean old results (5 minutes)
                for task_id in list(self.results.keys()):
                    if self.results[task_id]['created_at'] + 300 < now:
                        del self.results[task_id]
                        logger.debug(f"Cleaned old result {task_id}")
                        
            except Exception as e:
                logger.error(f"Cleanup error: {e}")
                
            await asyncio.sleep(30)  # Run every 30 seconds
            
    def _task_completed(self, result: CaptchaTaskResponse):
        """Callback when task is completed"""
        task_id = result.taskId
        self.results[task_id] = {
            'created_at': time(),
            'result': result
        }
        
        # Remove from active tasks
        if task_id in self.tasks:
            del self.tasks[task_id]
            
        logger.info(f"Task {task_id} completed with status: {result.status}")
        
    async def create_task(self):
        """Handle /createTask endpoint (project 2 format)"""
        try:
            data = await request.get_json()
            logger.info(f"Received createTask request: {data}")
            
            # Validate payload
            payload = CaptchaCreateTaskPayload(**data)
            
            # Check API key
            if payload.clientKey != os.getenv('API_KEY', 'default_key'):
                return jsonify({
                    "status": "error",
                    "errorId": 1,
                    "errorDescription": "Wrong clientKey"
                }), 400
                
            # Check task type
            if payload.task.type != 'AntiTurnstileTaskProxyLess':
                return jsonify({
                    "status": "error",
                    "errorId": 1,
                    "errorDescription": "Unsupported task type"
                }), 400
                
            # Generate task ID
            task_id = str(uuid.uuid4())
            payload.task.id = task_id
            
            # Store task
            self.tasks[task_id] = {
                'created_at': time(),
                'task': payload.task
            }
            
            # Submit to solver (using existing project 2 logic)
            await self.solver.add_task(payload.task)
            
            logger.success(f"Created task {task_id}")
            return jsonify({
                "status": "idle",
                "taskId": task_id
            }), 200
            
        except Exception as e:
            logger.error(f"Error in createTask: {e}")
            return jsonify({
                "status": "error",
                "errorId": 1,
                "errorDescription": str(e)
            }), 500
            
    async def get_task_result(self):
        """Handle /getTaskResult endpoint (project 2 format)"""
        try:
            data = await request.get_json()
            payload = CaptchaGetTaskPayload(**data)
            
            # Check API key
            if payload.clientKey != os.getenv('API_KEY', 'default_key'):
                return jsonify({
                    "status": "error",
                    "errorId": 1,
                    "errorDescription": "Wrong clientKey"
                }), 400
                
            task_id = payload.taskId
            
            # Check if result is ready
            if task_id in self.results:
                result = self.results[task_id]['result']
                response_data = result.json()
                
                # Log truncated token for privacy
                if result.status == 'ready' and 'solution' in response_data:
                    token = response_data['solution']['token']
                    logger.info(f"Returning result for {task_id}: {token[:50]}...")
                    
                return jsonify(response_data), 200
                
            # Check if still processing
            if task_id in self.tasks:
                return jsonify({
                    "status": "processing",
                    "taskId": task_id
                }), 200
                
            # Task not found
            return jsonify({
                "status": "error",
                "errorId": 1,
                "errorDescription": "Task not found or expired",
                "taskId": task_id
            }), 404
            
        except Exception as e:
            logger.error(f"Error in getTaskResult: {e}")
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
                    "error": "Missing url or sitekey parameter"
                }), 400
                
            # Create task
            task = CaptchaTask(
                type='AntiTurnstileTaskProxyLess',
                websiteURL=url,
                websiteKey=sitekey,
                id=str(uuid.uuid4())
            )
            
            # Store task
            self.tasks[task.id] = {
                'created_at': time(),
                'task': task
            }
            
            # Submit to solver
            await self.solver.add_task(task)
            
            return jsonify({
                "task_id": task.id
            }), 202
            
        except Exception as e:
            logger.error(f"Error in turnstile_simple: {e}")
            return jsonify({"error": str(e)}), 500
            
    async def get_result_simple(self):
        """Simple GET endpoint for result retrieval"""
        try:
            task_id = request.args.get('id')
            
            if not task_id:
                return jsonify({"error": "Missing id parameter"}), 400
                
            # Check result
            if task_id in self.results:
                result = self.results[task_id]['result']
                
                if result.status == 'ready':
                    return jsonify({
                        "status": "ready",
                        "value": result.solution['token']
                    }), 200
                else:
                    return jsonify({
                        "status": "error",
                        "error": result.errorDescription
                    }), 422
                    
            # Check if processing
            if task_id in self.tasks:
                return jsonify({
                    "status": "processing"
                }), 200
                
            return jsonify({
                "status": "error", 
                "error": "Task not found"
            }), 404
            
        except Exception as e:
            logger.error(f"Error in get_result_simple: {e}")
            return jsonify({"error": str(e)}), 500
            
    async def index(self):
        """API documentation homepage"""
        return f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>Turnstile Solver API</title>
            <style>
                body {{ font-family: Arial, sans-serif; margin: 40px; background: #f5f5f5; }}
                .container {{ background: white; padding: 30px; border-radius: 10px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }}
                h1 {{ color: #333; }}
                .endpoint {{ background: #f8f9fa; padding: 15px; margin: 10px 0; border-radius: 5px; }}
                .method-post {{ color: #dc3545; font-weight: bold; }}
                .method-get {{ color: #28a745; font-weight: bold; }}
                code {{ background: #e9ecef; padding: 2px 6px; border-radius: 3px; font-size: 12px; }}
                .status {{ color: #28a745; font-weight: bold; }}
            </style>
        </head>
        <body>
            <div class="container">
                <h1>🔓 Turnstile Solver API</h1>
                <p>API wrapper for existing browser automation logic</p>
                <p class="status">Status: ✅ Online | Workers: {self.max_workers}</p>
                
                <h2>Project 2 Format (Original)</h2>
                <div class="endpoint">
                    <h3><span class="method-post">POST</span> /createTask</h3>
                    <p>Create captcha solving task</p>
                    <code>{{"clientKey": "api_key", "task": {{"type": "AntiTurnstileTaskProxyLess", "websiteURL": "https://example.com", "websiteKey": "0x4AAA..."}}}}</code>
                </div>
                
                <div class="endpoint">
                    <h3><span class="method-post">POST</span> /getTaskResult</h3>
                    <p>Get task result</p>
                    <code>{{"clientKey": "api_key", "taskId": "uuid"}}</code>
                </div>
                
                <h2>Simple Format (External)</h2>
                <div class="endpoint">
                    <h3><span class="method-get">GET</span> /turnstile?url=...&sitekey=...</h3>
                    <p>Simple task creation</p>
                    <code>/turnstile?url=https://example.com&sitekey=0x4AAA...</code>
                </div>
                
                <div class="endpoint">
                    <h3><span class="method-get">GET</span> /result?id=...</h3>
                    <p>Simple result retrieval</p>
                    <code>/result?id=task_uuid</code>
                </div>
                
                <p><small>Powered by original browser automation logic from Project 2</small></p>
            </div>
        </body>
        </html>
        """

def create_app(max_workers: int = 3):
    """Factory function to create app"""
    wrapper = TurnstileAPIWrapper(max_workers)
    return wrapper.app

if __name__ == '__main__':
    import argparse
    
    parser = argparse.ArgumentParser(description="Turnstile API Server")
    parser.add_argument('--workers', type=int, default=3, help='Max workers')
    parser.add_argument('--host', type=str, default='0.0.0.0', help='Host')
    parser.add_argument('--port', type=int, default=5033, help='Port')
    args = parser.parse_args()
    
    app = create_app(max_workers=args.workers)
    
    logger.info(f"Starting Turnstile API Server")
    logger.info(f"Host: {args.host}:{args.port}")
    logger.info(f"Workers: {args.workers}")
    
    app.run(host=args.host, port=args.port)