"""
Smart Router - Automatic provider/model selection based on task type
Routes requests to optimal provider/model based on task characteristics
"""
import logging
import re
from typing import Dict, Any, Optional, Tuple
from enum import Enum

logger = logging.getLogger(__name__)


class TaskType(Enum):
    """Types of tasks the router can identify"""
    CODE = "code"
    CHAT = "chat"
    REASONING = "reasoning"
    SUMMARIZATION = "summarization"
    TRANSLATION = "translation"
    CREATIVE = "creative"
    UNKNOWN = "unknown"


class SmartRouter:
    """
    Intelligent router that selects optimal provider/model based on task type.
    Can be configured via routing rules in config.yaml
    """
    
    def __init__(self, config: Dict[str, Any]):
        """
        Initialize router with configuration.
        
        Args:
            config: Routing configuration from config.yaml
        """
        self.config = config
        self.default_provider = config.get('default_provider', 'ollama')
        self.default_model = config.get('default_model', 'fast')
        self.rules = config.get('rules', [])
        
        # Compile keyword patterns for task detection
        self._compile_patterns()
    
    def _compile_patterns(self):
        """Compile regex patterns for task type detection"""
        self.patterns = {
            TaskType.CODE: re.compile(
                r'\b(code|function|class|implement|program|script|algorithm|'
                r'debug|fix|refactor|python|javascript|java|go|rust)\b',
                re.IGNORECASE
            ),
            TaskType.REASONING: re.compile(
                r'\b(solve|calculate|analyze|figure out|reason|logic|'
                r'problem|equation|proof|deduce)\b',
                re.IGNORECASE
            ),
            TaskType.SUMMARIZATION: re.compile(
                r'\b(summarize|summary|brief|condense|tldr|key points|'
                r'main ideas|overview)\b',
                re.IGNORECASE
            ),
            TaskType.TRANSLATION: re.compile(
                r'\b(translate|translation|convert.*to.*language|'
                r'spanish|french|german|chinese)\b',
                re.IGNORECASE
            ),
            TaskType.CREATIVE: re.compile(
                r'\b(write.*story|poem|creative|imagine|invent|'
                r'brainstorm|novel|fiction)\b',
                re.IGNORECASE
            ),
        }
    
    def detect_task_type(self, prompt: str) -> TaskType:
        """
        Detect task type from prompt content.
        
        Args:
            prompt: The user's prompt
            
        Returns:
            Detected TaskType
        """
        # Check each pattern
        for task_type, pattern in self.patterns.items():
            if pattern.search(prompt):
                logger.debug(f"Detected task type: {task_type.value}")
                return task_type
        
        # Default to chat if no specific pattern matches
        logger.debug("No specific task type detected, using CHAT")
        return TaskType.CHAT
    
    def route(
        self,
        prompt: str,
        task_type: Optional[str] = None,
        provider: Optional[str] = None,
        model: Optional[str] = None
    ) -> Tuple[str, str]:
        """
        Route a request to appropriate provider and model.
        
        Args:
            prompt: The prompt to route
            task_type: Optional explicit task type
            provider: Optional explicit provider (overrides routing)
            model: Optional explicit model (overrides routing)
            
        Returns:
            Tuple of (provider_name, model_name)
        """
        # If explicit provider/model given, use those
        if provider and model:
            logger.info(f"Using explicit routing: {provider}/{model}")
            return (provider, model)
        
        # Detect task type if not provided
        if task_type is None:
            detected_type = self.detect_task_type(prompt)
            task_type = detected_type.value
        else:
            # Normalize to lowercase
            task_type = task_type.lower()
        
        # Find matching rule
        for rule in self.rules:
            if rule.get('task_type') == task_type:
                rule_provider = rule.get('provider', self.default_provider)
                rule_model = rule.get('model', self.default_model)
                
                logger.info(
                    f"Routing {task_type} task to {rule_provider}/{rule_model}"
                )
                
                return (
                    provider or rule_provider,
                    model or rule_model
                )
        
        # No rule found, use defaults
        logger.info(
            f"No rule for {task_type}, using default: "
            f"{self.default_provider}/{self.default_model}"
        )
        
        return (
            provider or self.default_provider,
            model or self.default_model
        )
    
    def get_provider_for_task(self, task_type: str) -> str:
        """Get provider name for a task type"""
        for rule in self.rules:
            if rule.get('task_type') == task_type:
                return rule.get('provider', self.default_provider)
        return self.default_provider
    
    def get_model_for_task(self, task_type: str) -> str:
        """Get model name for a task type"""
        for rule in self.rules:
            if rule.get('task_type') == task_type:
                return rule.get('model', self.default_model)
        return self.default_model
    
    def add_rule(
        self,
        task_type: str,
        provider: str,
        model: str,
        priority: int = 100
    ) -> None:
        """
        Add a routing rule dynamically.
        
        Args:
            task_type: Type of task (code, chat, etc.)
            provider: Provider to use
            model: Model to use
            priority: Rule priority (lower = higher priority)
        """
        rule = {
            'task_type': task_type,
            'provider': provider,
            'model': model,
            'priority': priority
        }
        
        self.rules.append(rule)
        
        # Sort by priority
        self.rules.sort(key=lambda r: r.get('priority', 100))
        
        logger.info(f"Added routing rule: {task_type} -> {provider}/{model}")
    
    def remove_rule(self, task_type: str) -> bool:
        """
        Remove a routing rule.
        
        Args:
            task_type: Type of task to remove rule for
            
        Returns:
            True if rule was removed, False if not found
        """
        original_len = len(self.rules)
        self.rules = [r for r in self.rules if r.get('task_type') != task_type]
        
        removed = len(self.rules) < original_len
        if removed:
            logger.info(f"Removed routing rule for: {task_type}")
        
        return removed
    
    def get_stats(self) -> Dict[str, Any]:
        """Get router statistics"""
        return {
            'default_provider': self.default_provider,
            'default_model': self.default_model,
            'rules_count': len(self.rules),
            'task_types_configured': [r['task_type'] for r in self.rules]
        }
    
    def __repr__(self) -> str:
        return (
            f"SmartRouter(default={self.default_provider}/{self.default_model}, "
            f"rules={len(self.rules)})"
        )
