"""
Structured Logging - JSON-formatted logs with context
"""
import logging
import json
import sys
from typing import Dict, Any, Optional
from datetime import datetime
from contextvars import ContextVar

# Context variables for request tracking
request_id_ctx: ContextVar[Optional[str]] = ContextVar('request_id', default=None)
provider_ctx: ContextVar[Optional[str]] = ContextVar('provider', default=None)
model_ctx: ContextVar[Optional[str]] = ContextVar('model', default=None)


class StructuredFormatter(logging.Formatter):
    """
    JSON formatter for structured logging.
    Includes context variables and standard fields.
    """
    
    def format(self, record: logging.LogRecord) -> str:
        """Format log record as JSON"""
        log_data = {
            'timestamp': datetime.utcnow().isoformat() + 'Z',
            'level': record.levelname,
            'logger': record.name,
            'message': record.getMessage(),
        }
        
        # Add context if available
        request_id = request_id_ctx.get()
        if request_id:
            log_data['request_id'] = request_id
        
        provider = provider_ctx.get()
        if provider:
            log_data['provider'] = provider
        
        model = model_ctx.get()
        if model:
            log_data['model'] = model
        
        # Add exception info if present
        if record.exc_info:
            log_data['exception'] = self.formatException(record.exc_info)
        
        # Add extra fields from record
        if hasattr(record, 'extra_fields'):
            log_data.update(record.extra_fields)
        
        return json.dumps(log_data)


class ContextAdapter(logging.LoggerAdapter):
    """
    Logger adapter that adds context fields to log records.
    """
    
    def process(self, msg, kwargs):
        """Add extra fields to log record"""
        extra = kwargs.get('extra', {})
        
        # Add context variables
        request_id = request_id_ctx.get()
        if request_id:
            extra['request_id'] = request_id
        
        provider = provider_ctx.get()
        if provider:
            extra['provider'] = provider
        
        model = model_ctx.get()
        if model:
            extra['model'] = model
        
        kwargs['extra'] = {'extra_fields': extra}
        return msg, kwargs


def setup_structured_logging(
    log_level: str = "INFO",
    use_json: bool = False,
    log_file: Optional[str] = None
):
    """
    Setup structured logging for the application.
    
    Args:
        log_level: Logging level (DEBUG, INFO, WARNING, ERROR)
        use_json: Whether to use JSON formatting
        log_file: Optional file path for logging
    """
    # Get root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, log_level.upper()))
    
    # Remove existing handlers
    root_logger.handlers.clear()
    
    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(getattr(logging, log_level.upper()))
    
    if use_json:
        console_handler.setFormatter(StructuredFormatter())
    else:
        console_handler.setFormatter(
            logging.Formatter(
                '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
            )
        )
    
    root_logger.addHandler(console_handler)
    
    # File handler if specified
    if log_file:
        file_handler = logging.FileHandler(log_file)
        file_handler.setLevel(getattr(logging, log_level.upper()))
        file_handler.setFormatter(StructuredFormatter())
        root_logger.addHandler(file_handler)


def get_logger(name: str) -> logging.Logger:
    """
    Get a logger with structured context support.
    
    Args:
        name: Logger name
        
    Returns:
        Logger instance
    """
    return logging.getLogger(name)


def set_request_context(
    request_id: Optional[str] = None,
    provider: Optional[str] = None,
    model: Optional[str] = None
):
    """
    Set context variables for the current request.
    
    Args:
        request_id: Unique request identifier
        provider: Provider name
        model: Model name
    """
    if request_id:
        request_id_ctx.set(request_id)
    if provider:
        provider_ctx.set(provider)
    if model:
        model_ctx.set(model)


def clear_request_context():
    """Clear all request context variables"""
    request_id_ctx.set(None)
    provider_ctx.set(None)
    model_ctx.set(None)
