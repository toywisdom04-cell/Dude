"""Intelligence abstraction for the DUDE Orchestrator.

Provides a clean interface that TaskEngine depends on, rather than
coupling directly to Ollama/Qwen or any specific provider.
"""
from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Optional


@dataclass
class ModelCapabilities:
    """What this backend can do."""
    streaming: bool = True
    tool_calling: bool = True
    vision: bool = False
    max_tokens: int = 4096
    context_window: int = 8192


@dataclass
class HealthStatus:
    """Backend health information."""
    healthy: bool
    message: str = ""
    latency_ms: float = 0.0
    model_loaded: bool = False


@dataclass
class GenerateRequest:
    """Request for non-streaming generation."""
    prompt: str
    system_prompt: str = ""
    temperature: float = 0.6
    max_tokens: int = 1024
    tools: list[dict] = field(default_factory=list)
    tool_choice: Optional[str] = None
    stop_sequences: list[str] = field(default_factory=list)


@dataclass
class GenerateResponse:
    """Response from non-streaming generation."""
    content: str
    tool_calls: list[dict] = field(default_factory=list)
    finish_reason: str = "stop"
    usage: Optional[dict] = None


@dataclass
class StreamRequest:
    """Request for streaming generation."""
    prompt: str
    system_prompt: str = ""
    temperature: float = 0.6
    max_tokens: int = 1024
    tools: list[dict] = field(default_factory=list)
    tool_choice: Optional[str] = None


class IntelligenceBackend(ABC):
    """Abstract interface for intelligence backends.
    
    TaskEngine depends on this interface, not on any specific provider.
    """
    
    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable name of this backend."""
        pass
    
    @property
    @abstractmethod
    def capabilities(self) -> ModelCapabilities:
        """What this backend can do."""
        pass
    
    @abstractmethod
    async def generate(self, request: GenerateRequest) -> GenerateResponse:
        """Non-streaming generation."""
        pass
    
    @abstractmethod
    async def stream(self, request: StreamRequest) -> AsyncIterator[str]:
        """Streaming generation - yields text deltas.
        
        For tool calls, the final chunk will contain a special marker
        or the implementation should provide a separate way to get tool calls.
        """
        pass
    
    @abstractmethod
    async def health(self) -> HealthStatus:
        """Check backend health."""
        pass
    
    @abstractmethod
    async def warm_up(self) -> bool:
        """Preload model if applicable. Returns True if successful."""
        pass


class IntelligenceBackendRegistry:
    """Registry of available intelligence backends."""
    
    def __init__(self):
        self._backends: dict[str, IntelligenceBackend] = {}
        self._default: Optional[str] = None
    
    def register(self, backend: IntelligenceBackend, default: bool = False) -> None:
        self._backends[backend.name] = backend
        if default or self._default is None:
            self._default = backend.name
    
    def get(self, name: Optional[str] = None) -> IntelligenceBackend:
        name = name or self._default
        if name not in self._backends:
            raise ValueError(f"Backend '{name}' not registered. Available: {list(self._backends.keys())}")
        return self._backends[name]
    
    def list(self) -> list[str]:
        return list(self._backends.keys())


# Global registry instance
_registry = IntelligenceBackendRegistry()


def get_registry() -> IntelligenceBackendRegistry:
    return _registry


def register_backend(backend: IntelligenceBackend, default: bool = False) -> None:
    _registry.register(backend, default)


def get_backend(name: Optional[str] = None) -> IntelligenceBackend:
    return _registry.get(name)