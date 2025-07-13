# api_wrapper.py - Improved version with health monitoring and auto-recovery

import asyncio
import uuid
import json
from time import time
import os
import sys
import subprocess
import signal

from quart import Quart, request, jsonify
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
        self.last_health_check = time()
        self.health_check_interval = 30  # 30 seconds
        self.restart_threshold = 5  # restart after 5 consecutive failures
        self.consecutive_failures = 0
        
        # Use existing project 2 components
        self.app_tasker = AppTasker()
        self.async_tasker = AsyncTasker(max_workers=max_workers, callback_fn=self.app_tasker.add_result)
        
        # Set solver in app_tasker
        self.app_tasker.solvers['AntiTurnstileTaskProxyLess'] = self.async_tasker
        
        # Health monitoring task
        self.health_monitor_task = None
        
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
        self.app.route('/health')(self.health)
        self.app.route('/reset', methods=['POST'])(self.force_reset)
        
    async def _startup(self):
        """Initialize on startup"""
        logger.info("🚀 Starting Docker Turnstile API...")
        logger.info(f"📊 Max workers: {self.max_workers}")
        
        # Start health monitoring
        self.health_monitor_task = asyncio.create_task(self._health_monitor())
        
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
        
        if self.health_monitor_task:
            self.health_monitor_task.cancel()
            try:
                await self.health_monitor_task
            except asyncio.CancelledError:
                pass
                
        # Force reset to cleanup resources
        await self.async_tasker.force_reset()
        
    async def _health_monitor(self):
        """Monitor system health and auto-recover"""
        while True:
            try:
                await asyncio.sleep(self.health_check_interval)
                
                # Get health status
                health_status = await self.async_tasker.health_check()
                
                # Log health status periodically
                if int(time()) % 300 == 0:  # Every 5 minutes
                    logger.info(f"📊 Health: {health_status}")
                
                # Check for issues
                issues = []
                
                # Circuit breaker is open
                if health_status['circuit_breaker_state'] == 'OPEN':
                    issues.append("Circuit breaker is OPEN")
                
                # No success in last 10 minutes
                if health_status['time_since_last_success'] > 600:
                    issues.append("No successful tasks in 10+ minutes")
                
                # Success rate too low
                if (health_status['total_tasks'] > 10 and 
                    health_status['success_rate'] < 20):
                    issues.append(f"Low success rate: {health_status['success_rate']:.1f}%")
                
                # Too many active tasks stuck
                if health_status['active_tasks'] > self.max_workers * 2:
                    issues.append(f"Too many active tasks: {health_status['active_tasks']}")
                
                # No available workers
                if health_status['available_workers'] == 0:
                    issues.append("No available workers")
                
                if issues:
                    self.consecutive_failures += 1
                    logger.warning(f"⚠️  Health issues detected ({self.consecutive_failures}): {', '.join(issues)}")
                    
                    # Auto-recovery if too many consecutive failures
                    if self.consecutive_failures >= self.restart_threshold:
                        logger.error(f"🔄 Auto-recovery triggered after {self.consecutive_failures} consecutive failures")
                        await self._auto_recover()
                        self.consecutive_failures = 0
                else:
                    self.consecutive_failures = 0
                    
            except Exception as e:
                logger.error(f"Health monitor error: {e}")
                await asyncio.sleep(5)
    
    async def _auto_recover(self):
        """Automatic recovery procedure"""
        try:
            logger.info("🔄 Starting auto-recovery...")
            
            # Force reset the async tasker
            await self.async_tasker.force_reset()
            
            # Clear app tasker data
            self.app_tasker.tasks.clear()
            self.app_tasker.results.clear()
            
            # Recreate async tasker
            self.async_tasker = AsyncTasker(
                max_workers=self.max_workers, 
                callback_fn=self.app_tasker.add_result
            )
            self.app_tasker.solvers['AntiTurnstileTaskProxyLess'] = self.async_tasker
            
            logger.success("✅ Auto-recovery completed")
            
        except Exception as e:
            logger.error(f"❌ Auto-recovery failed: {e}")

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

    async def health(self):
        """Detailed health endpoint"""
        try:
            health_status = await self.async_tasker.health_check()
            health_status.update({
                "api_status": "online",
                "consecutive_failures": self.consecutive_failures,
                "restart_threshold": self.restart_threshold,
                "uptime": time() - self.last_health_check,
                "environment": "docker",
                "display": os.environ.get('DISPLAY', 'not_set')
            })
            return jsonify(health_status), 200
        except Exception as e:
            return jsonify({
                "api_status": "error",
                "error": str(e)
            }), 500

    async def force_reset(self):
        """Force reset endpoint"""
        try:
            logger.warning("🔄 Manual reset triggered")
            await self._auto_recover()
            return jsonify({
                "status": "success",
                "message": "System reset completed"
            }), 200
        except Exception as e:
            logger.error(f"❌ Manual reset failed: {e}")
            return jsonify({
                "status": "error",
                "error": str(e)
            }), 500
            
    async def status(self):
        """API status endpoint"""
        return jsonify({
            "status": "online",
            "workers": self.max_workers,
            "active_tasks": len(self.app_tasker.tasks),
            "completed_results": len(self.app_tasker.results),
            "environment": "docker",
            "display": os.environ.get('DISPLAY', 'not_set'),
            "consecutive_failures": self.consecutive_failures
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
                .warning {{ background: #fff3cd; border: 1px solid #ffeaa7; padding: 10px; border-radius: 5px; margin: 10px 0; }}
            </style>
        </head>
        <body>
            <div class="container">
                <h1>🐳 Docker Turnstile Solver API</h1>
                
                <div class="status">
                    <strong>🟢 Status: Online</strong> | 
                    Workers: {self.max_workers} | 
                    Display: {os.environ.get('DISPLAY', 'not_set')} |
                    Failures: {self.consecutive_failures}/{self.restart_threshold}
                </div>
                
                {"<div class='warning'>⚠️ System experiencing issues - auto-recovery may trigger soon</div>" if self.consecutive_failures >= 3 else ""}
                
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
                
                <h3>Monitoring & Control</h3>
                <div class="endpoint">
                    <h4><span class="method-get">GET</span> /status</h4>
                    <p>Basic API status</p>
                    <code>/status</code>
                </div>
                
                <div class="endpoint">
                    <h4><span class="method-get">GET</span> /health</h4>
                    <p>Detailed health status with metrics</p>
                    <code>/health</code>
                </div>
                
                <div class="endpoint">
                    <h4><span class="method-post">POST</span> /reset</h4>
                    <p>Force system reset (emergency use)</p>
                    <code>/reset</code>
                </div>
                
                <p><small>🚀 Enhanced with auto-recovery and health monitoring</small></p>
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
    
    logger.info(f"🚀 Starting Enhanced Docker Turnstile API")
    logger.info(f"🌐 Host: {args.host}:{args.port}")
    logger.info(f"👥 Workers: {args.workers}")
    logger.info(f"🔑 API Key: {os.environ.get('API_KEY', 'not_set')}")
    logger.info(f"🖥️  Display: {os.environ.get('DISPLAY', 'not_set')}")
    logger.info(f"🔄 Auto-recovery enabled: failures > {api.restart_threshold}")
    
    # Run the app
    api.app.run(host=args.host, port=args.port, debug=False)

if __name__ == '__main__':
    main()