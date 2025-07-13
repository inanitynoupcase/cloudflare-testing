import asyncio
from time import time
from typing import Optional
import traceback
from dataclasses import dataclass

from loguru import logger

from browser import Browser, BrowserHandler
from models import CaptchaTask, CaptchaTaskResponse

@dataclass
class CircuitBreakerState:
    failure_count: int = 0
    last_failure_time: float = 0
    state: str = "CLOSED"  # CLOSED, OPEN, HALF_OPEN
    failure_threshold: int = 5
    recovery_timeout: int = 60

class Tasker:
    def __init__(self, max_workers: int = 1, callback_fn=None):
        self.max_workers = max_workers
        self.semaphore = asyncio.Semaphore(max_workers)
        self.callback_fn = callback_fn
        self.tasks = {}
        self.results = []
        self._last_clear = time()
        self.circuit_breaker = CircuitBreakerState()
        self.active_tasks = set()
        self.task_timeouts = {}
        
        # Health monitoring
        self.stats = {
            'total_tasks': 0,
            'successful_tasks': 0,
            'failed_tasks': 0,
            'timeout_tasks': 0,
            'last_success': time(),
        }

    def _update_circuit_breaker(self, success: bool):
        """Update circuit breaker state based on task result"""
        cb = self.circuit_breaker
        current_time = time()
        
        if success:
            if cb.state == "HALF_OPEN":
                logger.info("Circuit breaker: HALF_OPEN -> CLOSED (success)")
                cb.state = "CLOSED"
                cb.failure_count = 0
            elif cb.state == "CLOSED":
                cb.failure_count = max(0, cb.failure_count - 1)
            self.stats['successful_tasks'] += 1
            self.stats['last_success'] = current_time
        else:
            cb.failure_count += 1
            cb.last_failure_time = current_time
            self.stats['failed_tasks'] += 1
            
            if cb.state == "CLOSED" and cb.failure_count >= cb.failure_threshold:
                logger.warning(f"Circuit breaker: CLOSED -> OPEN (failures: {cb.failure_count})")
                cb.state = "OPEN"
            elif cb.state == "HALF_OPEN":
                logger.warning("Circuit breaker: HALF_OPEN -> OPEN (failure)")
                cb.state = "OPEN"
        
        # Auto recovery from OPEN to HALF_OPEN
        if (cb.state == "OPEN" and 
            current_time - cb.last_failure_time > cb.recovery_timeout):
            logger.info("Circuit breaker: OPEN -> HALF_OPEN (recovery timeout)")
            cb.state = "HALF_OPEN"
            cb.failure_count = 0

    def _should_reject_task(self) -> bool:
        """Check if task should be rejected based on circuit breaker"""
        cb = self.circuit_breaker
        
        if cb.state == "OPEN":
            return True
        
        # Additional health checks
        current_time = time()
        
        # If no success in last 5 minutes, be more conservative
        if current_time - self.stats['last_success'] > 300:
            return True
            
        # If failure rate is too high
        total_recent = self.stats['successful_tasks'] + self.stats['failed_tasks']
        if total_recent > 10:
            failure_rate = self.stats['failed_tasks'] / total_recent
            if failure_rate > 0.8:  # 80% failure rate
                return True
        
        return False

    async def add_task(self, task: CaptchaTask) -> None:
        """Add task with circuit breaker and improved error handling"""
        result = await self._add_task(task)
        if isinstance(result, CaptchaTaskResponse):
            if self.callback_fn:
                self.callback_fn(result)
            else:
                self.results.append(result)

    async def _add_task(self, task: CaptchaTask) -> Optional[CaptchaTaskResponse]:
        try:
            if isinstance(task, dict):
                task = CaptchaTask(**task)

            self.stats['total_tasks'] += 1

            # Circuit breaker check
            if self._should_reject_task():
                logger.warning(f"Task {task.id} rejected by circuit breaker")
                return CaptchaTaskResponse(
                    status='error',
                    taskId=task.id,
                    errorId=1, 
                    errorDescription='Service temporarily unavailable (circuit breaker)')

            # Overload check
            if len(self.tasks) > self.max_workers * 3:
                logger.warning(f'Overloaded: tasks={len(self.tasks)}, workers={self.max_workers}')
                return CaptchaTaskResponse(
                    status='error',
                    taskId=task.id,
                    errorId=1, 
                    errorDescription='The solver is overloaded')

            self.tasks[task.id] = {'t': time(), 'task': task}
            self.active_tasks.add(task.id)
            
            # Set task timeout
            self.task_timeouts[task.id] = asyncio.create_task(
                self._timeout_task(task.id, 120)  # 2 minute timeout per task
            )
            
            # Start solving
            asyncio.create_task(self.solve(task))

        except Exception as er:
            logger.warning(f"Error adding task {task.id}: {er}")
            self._cleanup_task(task.id)
            return CaptchaTaskResponse(
                status='error', 
                taskId=task.id, 
                errorId=1, 
                errorDescription=f'{er.__class__.__name__}: {er}')

    async def _timeout_task(self, task_id: str, timeout_seconds: int):
        """Handle task timeout"""
        try:
            await asyncio.sleep(timeout_seconds)
            if task_id in self.active_tasks:
                logger.warning(f"Task {task_id} timed out after {timeout_seconds}s")
                self.stats['timeout_tasks'] += 1
                self._cleanup_task(task_id)
                
                result = CaptchaTaskResponse(
                    taskId=task_id,
                    status='error',
                    errorId=1,
                    errorDescription='Task timeout')
                
                if self.callback_fn:
                    self.callback_fn(result)
                else:
                    self.results.append(result)
                    
        except asyncio.CancelledError:
            pass

    def _cleanup_task(self, task_id: str):
        """Clean up task resources"""
        try:
            if task_id in self.tasks:
                del self.tasks[task_id]
            if task_id in self.active_tasks:
                self.active_tasks.remove(task_id)
            if task_id in self.task_timeouts:
                self.task_timeouts[task_id].cancel()
                del self.task_timeouts[task_id]
        except Exception as e:
            logger.debug(f"Cleanup error for task {task_id}: {e}")

    async def solve(self, task: CaptchaTask):
        """Solve task with improved error handling and monitoring"""
        start_time = time()
        result = None
        
        try:
            async with self.semaphore:
                logger.debug(f"Starting to solve task {task.id}")
                
                # Check if task was cancelled
                if task.id not in self.active_tasks:
                    logger.debug(f"Task {task.id} was cancelled")
                    return
                
                browser = Browser()
                token = await browser.solve_captcha(task)
                
                if token:
                    result = CaptchaTaskResponse(
                        taskId=task.id,
                        status='ready',
                        solution={
                            'token': token,
                            'type': task.type
                        })
                    self._update_circuit_breaker(success=True)
                    logger.success(f"Task {task.id} completed in {time() - start_time:.1f}s")
                else:
                    result = CaptchaTaskResponse(
                        taskId=task.id,
                        status='error',
                        errorId=1,
                        errorDescription='Token not found')
                    self._update_circuit_breaker(success=False)
                    logger.warning(f"Task {task.id} failed: token not found")

        except asyncio.CancelledError:
            logger.debug(f"Task {task.id} was cancelled")
            return
        except Exception as er:
            result = CaptchaTaskResponse(
                taskId=task.id,
                status='error',
                errorId=1,
                errorDescription=f'{er.__class__.__name__}: {er}')
            self._update_circuit_breaker(success=False)
            logger.error(f"Task {task.id} failed with exception: {er}")
            logger.debug(traceback.format_exc())
        finally:
            self._cleanup_task(task.id)

        # Send result
        if result and self.callback_fn:
            self.callback_fn(result)
        elif result:
            self.results.append(result)

    async def health_check(self) -> dict:
        """Return health status"""
        cb = self.circuit_breaker
        current_time = time()
        
        return {
            'circuit_breaker_state': cb.state,
            'circuit_breaker_failures': cb.failure_count,
            'active_tasks': len(self.active_tasks),
            'queued_tasks': len(self.tasks),
            'total_tasks': self.stats['total_tasks'],
            'successful_tasks': self.stats['successful_tasks'],
            'failed_tasks': self.stats['failed_tasks'],
            'timeout_tasks': self.stats['timeout_tasks'],
            'success_rate': (self.stats['successful_tasks'] / max(1, self.stats['total_tasks'])) * 100,
            'time_since_last_success': current_time - self.stats['last_success'],
            'available_workers': self.semaphore._value,
        }

    async def force_reset(self):
        """Force reset all tasks and circuit breaker"""
        logger.warning("Force resetting tasker...")
        
        # Cancel all timeout tasks
        for timeout_task in self.task_timeouts.values():
            timeout_task.cancel()
        
        # Clear all data structures
        self.tasks.clear()
        self.active_tasks.clear()
        self.task_timeouts.clear()
        
        # Reset circuit breaker
        self.circuit_breaker = CircuitBreakerState()
        
        # Reset browser handler
        try:
            await BrowserHandler().cleanup_all()
        except Exception as e:
            logger.error(f"Error during browser cleanup: {e}")
        
        logger.info("Tasker reset completed")