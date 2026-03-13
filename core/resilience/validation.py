"""
Input Validation and Safety - Protect against malicious or invalid inputs
"""
import re
from typing import Any, Optional, List
from dataclasses import dataclass

from core.resilience.errors import InvalidInputError


@dataclass
class ValidationRules:
    """Rules for input validation"""
    min_length: Optional[int] = None
    max_length: Optional[int] = None
    max_tokens: Optional[int] = None
    allowed_chars: Optional[str] = None  # Regex pattern
    forbidden_patterns: Optional[List[str]] = None
    max_temperature: float = 2.0
    min_temperature: float = 0.0


# Default validation rules
DEFAULT_RULES = ValidationRules(
    max_length=100000,  # 100K characters
    max_tokens=8192,    # Common model limit
    max_temperature=2.0,
    min_temperature=0.0,
    forbidden_patterns=[
        r'<script[^>]*>.*?</script>',  # JavaScript injection
        r'javascript:',                 # JavaScript protocol
        r'on\w+\s*=',                  # Event handlers
    ]
)


def validate_prompt(prompt: str, rules: Optional[ValidationRules] = None) -> str:
    """
    Validate and sanitize prompt input.
    
    Args:
        prompt: User prompt
        rules: ValidationRules instance
        
    Returns:
        Sanitized prompt
        
    Raises:
        InvalidInputError: If validation fails
    """
    if rules is None:
        rules = DEFAULT_RULES
    
    # Check type
    if not isinstance(prompt, str):
        raise InvalidInputError(
            field="prompt",
            value=type(prompt).__name__,
            reason="Prompt must be a string"
        )
    
    # Check not empty
    if not prompt.strip():
        raise InvalidInputError(
            field="prompt",
            value="",
            reason="Prompt cannot be empty"
        )
    
    # Check min length
    if rules.min_length and len(prompt) < rules.min_length:
        raise InvalidInputError(
            field="prompt",
            value=len(prompt),
            reason=f"Prompt too short (min: {rules.min_length})"
        )
    
    # Check max length
    if rules.max_length and len(prompt) > rules.max_length:
        raise InvalidInputError(
            field="prompt",
            value=len(prompt),
            reason=f"Prompt too long (max: {rules.max_length})"
        )
    
    # Check forbidden patterns
    if rules.forbidden_patterns:
        for pattern in rules.forbidden_patterns:
            if re.search(pattern, prompt, re.IGNORECASE):
                raise InvalidInputError(
                    field="prompt",
                    value=prompt[:100] + "...",
                    reason=f"Prompt contains forbidden pattern"
                )
    
    # Sanitize: remove null bytes and control characters
    sanitized = prompt.replace('\x00', '')
    sanitized = ''.join(char for char in sanitized if ord(char) >= 32 or char in '\n\r\t')
    
    return sanitized


def validate_temperature(temperature: float, rules: Optional[ValidationRules] = None) -> float:
    """
    Validate temperature parameter.
    
    Args:
        temperature: Temperature value
        rules: ValidationRules instance
        
    Returns:
        Validated temperature
        
    Raises:
        InvalidInputError: If validation fails
    """
    if rules is None:
        rules = DEFAULT_RULES
    
    # Check type
    if not isinstance(temperature, (int, float)):
        raise InvalidInputError(
            field="temperature",
            value=temperature,
            reason="Temperature must be a number"
        )
    
    # Check range
    if temperature < rules.min_temperature:
        raise InvalidInputError(
            field="temperature",
            value=temperature,
            reason=f"Temperature too low (min: {rules.min_temperature})"
        )
    
    if temperature > rules.max_temperature:
        raise InvalidInputError(
            field="temperature",
            value=temperature,
            reason=f"Temperature too high (max: {rules.max_temperature})"
        )
    
    return float(temperature)


def validate_max_tokens(max_tokens: int, rules: Optional[ValidationRules] = None) -> int:
    """
    Validate max_tokens parameter.
    
    Args:
        max_tokens: Maximum tokens
        rules: ValidationRules instance
        
    Returns:
        Validated max_tokens
        
    Raises:
        InvalidInputError: If validation fails
    """
    if rules is None:
        rules = DEFAULT_RULES
    
    # Check type
    if not isinstance(max_tokens, int):
        raise InvalidInputError(
            field="max_tokens",
            value=max_tokens,
            reason="max_tokens must be an integer"
        )
    
    # Check positive
    if max_tokens <= 0:
        raise InvalidInputError(
            field="max_tokens",
            value=max_tokens,
            reason="max_tokens must be positive"
        )
    
    # Check limit
    if rules.max_tokens and max_tokens > rules.max_tokens:
        raise InvalidInputError(
            field="max_tokens",
            value=max_tokens,
            reason=f"max_tokens exceeds limit ({rules.max_tokens})"
        )
    
    return max_tokens


def validate_model_name(model: str) -> str:
    """
    Validate model name.
    
    Args:
        model: Model name
        
    Returns:
        Validated model name
        
    Raises:
        InvalidInputError: If validation fails
    """
    # Check type
    if not isinstance(model, str):
        raise InvalidInputError(
            field="model",
            value=type(model).__name__,
            reason="Model must be a string"
        )
    
    # Check not empty
    if not model.strip():
        raise InvalidInputError(
            field="model",
            value="",
            reason="Model name cannot be empty"
        )
    
    # Check format (alphanumeric, dots, colons, hyphens, underscores)
    if not re.match(r'^[a-zA-Z0-9._:-]+$', model):
        raise InvalidInputError(
            field="model",
            value=model,
            reason="Model name contains invalid characters"
        )
    
    # Check length
    if len(model) > 100:
        raise InvalidInputError(
            field="model",
            value=model,
            reason="Model name too long (max: 100)"
        )
    
    return model


def sanitize_output(output: str, max_length: Optional[int] = None) -> str:
    """
    Sanitize model output.
    
    Args:
        output: Raw model output
        max_length: Maximum output length
        
    Returns:
        Sanitized output
    """
    if not isinstance(output, str):
        return str(output)
    
    # Remove null bytes
    sanitized = output.replace('\x00', '')
    
    # Truncate if needed
    if max_length and len(sanitized) > max_length:
        sanitized = sanitized[:max_length] + "..."
    
    return sanitized


def check_resource_limits(
    prompt_length: int,
    max_tokens: int,
    max_total_length: int = 100000
) -> bool:
    """
    Check if request is within resource limits.
    
    Args:
        prompt_length: Length of prompt
        max_tokens: Requested max tokens
        max_total_length: Maximum total length
        
    Returns:
        True if within limits
        
    Raises:
        InvalidInputError: If limits exceeded
    """
    total_length = prompt_length + max_tokens
    
    if total_length > max_total_length:
        raise InvalidInputError(
            field="total_length",
            value=total_length,
            reason=f"Total length exceeds limit ({max_total_length})"
        )
    
    return True
