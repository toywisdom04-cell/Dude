"""Adapter wrapping existing Brain to implement IntelligenceBackend."""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any, AsyncIterator, Optional

from .intelligence import (
    IntelligenceBackend,
    ModelCapabilities,
    HealthStatus,
    GenerateRequest,
    GenerateResponse,
    StreamRequest,
)

from core.brain import Brain, BrainUnavailable
from core.tools import tool_specs


class BrainAdapter(IntelligenceBackend):
    """Wraps existing core.brain.Brain to implement IntelligenceBackend interface."""
    
    def __init__(self, brain: Brain):
        self._brain = brain
        self._name = "brain-ollama-qwen"
    
    @property
    def name(self) -> str:
        return self._name
    
    @property
    def capabilities(self) -> ModelCapabilities:
        # Check what the active provider supports
        provider = self._brain.active_provider
        return ModelCapabilities(
            streaming=True,
            tool_calling=True,
            vision=provider.vision if provider else False,
            max_tokens=4096,
            context_window=8192,
        )
    
    async def generate(self, request: GenerateRequest) -> GenerateResponse:
        """Non-streaming generation using brain.think()."""
        try:
            # brain.think() is the non-streaming path
            content = self._brain.think(request.prompt, system_extra=request.system_prompt)
            
            # Check if response contains tool calls (simplified)
            tool_calls = []
            if "tool_calls" in content.lower() or '"name"' in content:
                # Very basic detection - real implementation would parse properly
                pass
            
            return GenerateResponse(
                content=content,
                tool_calls=tool_calls,
                finish_reason="stop",
            )
        except BrainUnavailable as e:
            raise RuntimeError(f"Brain unavailable: {e}")
        except Exception as e:
            raise RuntimeError(f"Generation failed: {e}")
    
    async def stream(self, request: StreamRequest) -> AsyncIterator[str]:
        """Streaming generation using brain.chat() with callbacks."""
        # We need to capture the streamed output
        queue: asyncio.Queue[str] = asyncio.Queue()
        finished = asyncio.Event()
        error: Optional[Exception] = None
        
        def on_delta(text: str):
            try:
                queue.put_nowait(text)
            except Exception:
                pass
        
        def on_tool(tool_name: str):
            # Could yield a special marker for tool calls
            pass
        
        def run_chat():
            nonlocal error
            try:
                # Build full prompt with system
                full_prompt = request.prompt
                if request.system_prompt:
                    full_prompt = f"{request.system_prompt}\n\n{full_prompt}"
                
                self._brain.chat(
                    full_prompt,
                    on_delta=on_delta,
                    on_tool=on_tool,
                    system_extra="",
                )
            except Exception as e:
                error = e
            finally:
                finished.set()
        
        # Run brain.chat in executor since it's blocking
        loop = asyncio.get_event_loop()
        loop.run_in_executor(None, run_chat)
        
        # Yield chunks as they arrive
        while not finished.is_set() or not queue.empty():
            try:
                chunk = await asyncio.wait_for(queue.get(), timeout=0.1)
                yield chunk
            except asyncio.TimeoutError:
                continue
        
        if error:
            raise error
    
    async def health(self) -> HealthStatus:
        """Check if brain is responsive."""
        try:
            start = time.time()
            # Quick health check - try a tiny prompt
            result = self._brain.think("ready", system_extra="Reply with 'ok'")
            latency = (time.time() - start) * 1000
            
            provider = self._brain.active_provider
            return HealthStatus(
                healthy=True,
                message=f"OK via {provider.name if provider else 'unknown'}",
                latency_ms=latency,
                model_loaded=provider is not None,
            )
        except Exception as e:
            return HealthStatus(
                healthy=False,
                message=str(e),
                latency_ms=0,
                model_loaded=False,
            )
    
    async def warm_up(self) -> bool:
        """Preload the model via brain.warm_llm()."""
        try:
            return self._brain.warm_llm()
        except Exception:
            return False


def create_brain_adapter(brain: Brain) -> BrainAdapter:
    """Factory to create a BrainAdapter from existing Brain instance."""
    return BrainAdapter(brain)