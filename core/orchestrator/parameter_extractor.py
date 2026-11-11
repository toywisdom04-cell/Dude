"""Parameter Extraction for DUDE Orchestrator.

Extracts structured parameters from natural language commands for procedure binding.
Uses deterministic pattern matching - no LLM required.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional
from typing import Pattern

from core.orchestrator.state import ProcedureParameter


@dataclass
class ExtractedParameter:
    """A parameter extracted from user input."""
    name: str
    value: Any
    confidence: float = 1.0
    source: str = "pattern"  # pattern, context, default


@dataclass
class ExtractionResult:
    """Result of parameter extraction."""
    parameters: dict[str, Any] = field(default_factory=dict)
    missing_required: list[str] = field(default_factory=list)
    confidence: float = 1.0
    warnings: list[str] = field(default_factory=list)


class ParameterExtractor:
    """Extracts structured parameters from natural language using deterministic patterns."""
    
    def __init__(self):
        self._patterns: list[ParameterPattern] = []
        self._register_builtin_patterns()
    
    def _register_builtin_patterns(self):
        """Register built-in parameter extraction patterns."""
        # Folder/directory creation patterns
        self.register(ParameterPattern(
            name="folder_name",
            param_name="folder_name",
            patterns=[
                r"(?:create|make|mkdir)\s+(?:a\s+)?(?:folder|directory)\s+(?:named|called)\s+([a-zA-Z0-9_\-\.]+)",
                r"(?:create|make|mkdir)\s+([a-zA-Z0-9_\-\.]+)\s+(?:folder|directory)",
                r"new\s+(?:folder|directory)\s+(?:named|called)\s+([a-zA-Z0-9_\-\.]+)",
                # "create folder X" format
                r"(?:create|make|mkdir)\s+(?:folder|directory)\s+([a-zA-Z0-9_\-\.]+)",
            ],
            param_type="directory",
            required=True,
        ))
        
        # File creation patterns
        self.register(ParameterPattern(
            name="file_path",
            param_name="path",
            patterns=[
                r"(?:create|make|write)\s+(?:a\s+)?(?:file\s+)?(?:named|called)\s+([a-zA-Z0-9_\-\.]+)",
                # Bare pronouns are never paths ("Save it as ..." must
                # not extract path='it').
                r"(?:write|save)\s+(?:to\s+)?(?!it\b|this\b|that\b|them\b|the\b)([a-zA-Z0-9_\-\.\/\\]+)",
            ],
            param_type="path",
            required=True,
        ))
        
        # Save-as filename: "save it as notes.txt". Optional so goals
        # without an explicit filename keep extracting as before; when
        # present it lets repeated saves generalize across filenames.
        self.register(ParameterPattern(
            name="save_filename",
            param_name="filename",
            patterns=[
                r"save\s+(?:it\s+)?as\s+[\"']([^\"']+)[\"']",
                r"save\s+(?:it\s+)?as\s+([A-Za-z0-9_\-\.]+)",
            ],
            param_type="filename",
            required=False,
        ))

        self.register(ParameterPattern(
            name="file_content",
            param_name="content",
            patterns=[
                r"(?:write|save)\s+(?:text\s+)?[\"']([^\"']+)[\"']\s+(?:to|in)\s+([a-zA-Z0-9_\-\.\/\\]+)",
                r"(?:write|put)\s+[\"']([^\"']+)[\"']",
                # Unquoted authored content: "create a text document
                # containing: <words>". Runs to the sentence end or
                # the next clause verb, never swallowing it.
                r"contain(?:ing|s)?\s*:?\s*[\"']?([^\"']+?)[\"']?(?=\.\s+|\s+[Ss]ave\b|\s*$)",
            ],
            param_type="string",
            required=False,
        ))
        
        # App launch patterns. The verb must open a clause (start,
        # punctuation, then/and/please) so nouns like "second run
        # content" never extract an app, and the value is bounded to a
        # few words so the rest of the sentence is not swallowed.
        self.register(ParameterPattern(
            name="app_name",
            param_name="app_name",
            patterns=[
                r"(?:^|[.,;!?]\s*|\bthen\b\s*|\band\b\s*|\bplease\b\s*)(?:open|launch|start|run)\s+([a-zA-Z0-9_\-]+(?:\s+[a-zA-Z0-9_\-]+){0,3})",
            ],
            param_type="app_name",
            required=True,
        ))
        
        # Move/rename patterns
        self.register(ParameterPattern(
            name="move_source",
            param_name="source",
            patterns=[
                r"move\s+([a-zA-Z0-9_\-\.\/\\]+)\s+(?:to|into)\s+([a-zA-Z0-9_\-\.\/\\]+)",
            ],
            param_type="path",
            required=True,
        ))
        
        self.register(ParameterPattern(
            name="move_destination",
            param_name="destination",
            patterns=[
                r"move\s+([a-zA-Z0-9_\-\.\/\\]+)\s+(?:to|into)\s+([a-zA-Z0-9_\-\.\/\\]+)",
            ],
            param_type="path",
            required=True,
        ))
        
        # Copy patterns
        self.register(ParameterPattern(
            name="copy_source",
            param_name="source",
            patterns=[
                r"copy\s+([a-zA-Z0-9_\-\.\/\\]+)\s+(?:to|into)\s+([a-zA-Z0-9_\-\.\/\\]+)",
            ],
            param_type="path",
            required=True,
        ))
        
        self.register(ParameterPattern(
            name="copy_destination",
            param_name="destination",
            patterns=[
                r"copy\s+([a-zA-Z0-9_\-\.\/\\]+)\s+(?:to|into)\s+([a-zA-Z0-9_\-\.\/\\]+)",
            ],
            param_type="path",
            required=True,
        ))
        
        # Type text patterns
        self.register(ParameterPattern(
            name="type_text",
            param_name="text",
            patterns=[
                r"(?:type|write)\s+[\"']([^\"']+)[\"']",
                r"(?:type|write)\s+(.+)",
            ],
            param_type="string",
            required=True,
        ))
        
        # Hotkey patterns
        self.register(ParameterPattern(
            name="hotkey",
            param_name="keys",
            patterns=[
                r"(?:press|hit)\s+(?:the\s+)?([a-zA-Z0-9\+\-\s]+)(?:\s+key)?",
            ],
            param_type="string",
            required=True,
        ))

        # Arithmetic operands: "calculate 25 times 4", "compute 8 + 8".
        # Operands/operator/expected-result become parameters so repeated
        # calculations generalize to one procedure with different values.
        # Generic number handling, not application-specific. Each pattern
        # puts its own operand in group 1 (_match_pattern takes group 1).
        _arith_op = (r"(?:\+|-|times|multiplied\s+by|multiply|plus|minus|"
                     r"subtract|divided\s+by|divide|[x×÷/])")
        _arith_n = r"(\d+(?:\.\d+)?)"
        self.register(ParameterPattern(
            name="operand1",
            param_name="operand1",
            patterns=[r"(?:calculate|compute|work\s+out|evaluate|solve)\s+"
                      + _arith_n + r"\s*" + _arith_op],
            param_type="number",
            required=False,
        ))
        self.register(ParameterPattern(
            name="operand2",
            param_name="operand2",
            patterns=[r"(?:calculate|compute|work\s+out|evaluate|solve)\s+"
                      r"\d+(?:\.\d+)?\s*" + _arith_op + r"\s*" + _arith_n],
            param_type="number",
            required=False,
        ))
        self.register(ParameterPattern(
            name="operator",
            param_name="operator",
            patterns=[r"(?:calculate|compute|work\s+out|evaluate|solve)\s+"
                      r"\d+(?:\.\d+)?\s*"
                      r"(\+|-|times|multiplied\s+by|multiply|plus|minus|"
                      r"subtract|divided\s+by|divide|[x×÷/])"],
            param_type="string",
            required=False,
        ))
        self.register(ParameterPattern(
            name="expected_result",
            param_name="expected_result",
            patterns=[
                r"(?:result|answer|display)(?:\s+is)?\s*:?\s*"
                r"(\d+(?:\.\d+)?)\s*[.\s]*$",
            ],
            param_type="number",
            required=False,
        ))
    
    def register(self, pattern: "ParameterPattern"):
        """Register a parameter extraction pattern."""
        self._patterns.append(pattern)
    
    def extract(self, text: str, procedure_params: list = None) -> ExtractionResult:
        """Extract parameters from text."""
        result = ExtractionResult()
        
        # If procedure has specific parameters, try to match those first
        if procedure_params:
            for param in procedure_params:
                value = self._extract_single(text, param)
                if value is not None:
                    result.parameters[param.name] = value.value
                elif param.required:
                    result.missing_required.append(param.name)
                    result.confidence = max(0.0, result.confidence - 0.3)
        
        # Also try general patterns
        for pattern in self._patterns:
            if pattern.param_name in result.parameters:
                continue  # Already extracted
            value = self._match_pattern(text, pattern)
            if value is not None:
                # Validate the value
                validated = self._validate_value(value, pattern.param_type)
                if validated is not None:
                    result.parameters[pattern.param_name] = validated
        
        # Calculate overall confidence
        if result.missing_required:
            result.confidence = max(0.0, result.confidence - 0.2 * len(result.missing_required))
        
        return result
    
    def _extract_single(self, text: str, param: ProcedureParameter) -> Optional[ExtractedParameter]:
        """Extract a single parameter by its definition."""
        # Try to find pattern for this parameter
        for pattern in self._patterns:
            if pattern.param_name == param.name:
                value = self._match_pattern(text, pattern)
                if value is not None:
                    validated = self._validate_value(value, param.type)
                    if validated is not None:
                        return ExtractedParameter(
                            name=param.name,
                            value=validated,
                            confidence=0.9,
                            source="pattern"
                        )
        return None
    
    def _match_pattern(self, text: str, pattern: "ParameterPattern") -> Optional[str]:
        """Match a pattern against text and return the first capture group."""
        for pattern_regex in pattern.patterns:
            match = re.search(pattern_regex, text, re.IGNORECASE)
            if match:
                # Return first capture group, or whole match if no groups
                if match.groups():
                    return match.group(1).strip()
                return match.group(0).strip()
        return None
    
    def _validate_value(self, value: str, param_type: str) -> Optional[str]:
        """Validate and sanitize a value based on its type."""
        if not value:
            return None
        
        if param_type == "directory":
            # Sanitize directory name
            sanitized = re.sub(r'[<>:"|?*]', '', value).strip()
            if not sanitized:
                return None
            return sanitized
        
        elif param_type == "path" or param_type == "filename":
            # Sanitize path
            sanitized = value.strip()
            # Remove potentially dangerous patterns
            if '..' in sanitized or sanitized.startswith('/'):
                return None
            return sanitized
        
        elif param_type == "app_name":
            # App names are relatively flexible, but must not swallow a
            # compound goal ("Notepad and type hello" -> "Notepad").
            cleaned = re.sub(r'\s+', ' ', value).strip().rstrip('.,!?;:')
            cleaned = re.split(r'\s+(?:and|then)\s+', cleaned, maxsplit=1)[0].strip()
            return cleaned or None
        
        elif param_type == "string":
            return value.strip()
        
        elif param_type == "integer":
            try:
                return str(int(value))
            except ValueError:
                return None
        
        elif param_type == "boolean":
            lower = value.lower()
            if lower in ("true", "yes", "1", "on"):
                return "true"
            elif lower in ("false", "no", "0", "off"):
                return "false"
            return None
        
        return value.strip()


@dataclass
class ParameterPattern:
    """A pattern for extracting a specific parameter."""
    name: str
    param_name: str
    patterns: list[str]
    param_type: str = "string"
    required: bool = True


# Global extractor instance
_extractor: Optional[ParameterExtractor] = None


def get_parameter_extractor() -> ParameterExtractor:
    """Get the global parameter extractor."""
    global _extractor
    if _extractor is None:
        _extractor = ParameterExtractor()
    return _extractor