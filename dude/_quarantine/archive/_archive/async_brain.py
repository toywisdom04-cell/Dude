"""Async execution wrapper for non-blocking brain calls.

Keeps the main event loop responsive while LLM reasoning happens in a background thread.
"""

import threading
import queue
import time
import logging

log = logging.getLogger("dude")

class AsyncBrainExecutor:
    """Run brain reasoning in background thread without blocking main loop."""
    
    def __init__(self, brain):
        self.brain = brain
        self.result_queue = queue.Queue(maxsize=1)
        self._thread = None
        self._stop = threading.Event()
    
    def execute_async(self, messages, on_delta=None, timeout=60):
        """Start brain execution in background thread.
        
        Returns immediately with a callable that retrieves results.
        """
        if self._thread and self._thread.is_alive():
            # Wait for previous execution to complete
            try:
                self.result_queue.get(timeout=2)
            except queue.Empty:
                pass
        
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run_brain,
            args=(messages, on_delta),
            daemon=True,
            name="du-brain-async"
        )
        self._thread.start()
        
        # Return a waiter function
        return lambda t=timeout: self._wait_result(t)
    
    def _run_brain(self, messages, on_delta):
        """Background thread execution."""
        try:
            result = self.brain.reply(messages, on_delta=on_delta)
            self.result_queue.put(("success", result))
        except Exception as e:
            log.warning(f"Brain async execution failed: {e}")
            self.result_queue.put(("error", str(e)))
    
    def _wait_result(self, timeout):
        """Wait for brain result (blocking, but called by user only when needed)."""
        try:
            status, result = self.result_queue.get(timeout=timeout)
            if status == "success":
                return result
            else:
                raise RuntimeError(f"Brain error: {result}")
        except queue.Empty:
            raise TimeoutError(f"Brain reasoning timeout after {timeout}s")
    
    def cancel(self):
        """Cancel ongoing brain execution."""
        self._stop.set()
        if self.brain:
            self.brain._cancel_requested = True

class StreamingBrainDelta:
    """Collects streaming delta callbacks for incremental response handling."""
    
    def __init__(self, on_sentence=None):
        self.on_sentence = on_sentence or (lambda s: None)
        self._lock = threading.Lock()
        self._sentences = []
    
    def __call__(self, text):
        """Called by brain for each streaming delta."""
        with self._lock:
            self._sentences.append(text)
        # Call callback for UI/voice display
        self.on_sentence(text)
    
    def get_accumulated(self):
        """Get all accumulated text."""
        with self._lock:
            return " ".join(self._sentences)
