"""
Workflow Orchestrator - Chain multiple LLM operations
Enables RAG, multi-step reasoning, and complex pipelines
"""
import asyncio
import logging
import time
from typing import Dict, Any, List, Callable, Optional, Union
from dataclasses import dataclass, field
from enum import Enum
from core.observability import get_metrics_collector

logger = logging.getLogger(__name__)


class StepType(Enum):
    """Types of workflow steps"""
    LLM = "llm"              # LLM generation
    FUNCTION = "function"     # Custom function
    PARALLEL = "parallel"     # Run multiple steps in parallel
    CONDITIONAL = "conditional"  # Conditional branching


@dataclass
class WorkflowContext:
    """
    Context passed between workflow steps.
    Stores inputs, outputs, and intermediate results.
    """
    inputs: Dict[str, Any] = field(default_factory=dict)
    outputs: Dict[str, Any] = field(default_factory=dict)
    steps_executed: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def set(self, key: str, value: Any) -> None:
        """Set a value in the context"""
        self.outputs[key] = value
    
    def get(self, key: str, default: Any = None) -> Any:
        """Get a value from context (checks outputs first, then inputs)"""
        if key in self.outputs:
            return self.outputs[key]
        return self.inputs.get(key, default)
    
    def has(self, key: str) -> bool:
        """Check if key exists in context"""
        return key in self.outputs or key in self.inputs


@dataclass
class WorkflowStep:
    """Represents a single step in a workflow"""
    name: str
    step_type: StepType
    config: Dict[str, Any] = field(default_factory=dict)
    
    # For LLM steps
    prompt_template: Optional[str] = None
    provider: Optional[str] = None
    model: Optional[str] = None
    
    # For function steps
    function: Optional[Callable] = None
    
    # For parallel steps
    parallel_steps: Optional[List['WorkflowStep']] = None
    
    # For conditional steps
    condition: Optional[Callable] = None
    if_true: Optional['WorkflowStep'] = None
    if_false: Optional['WorkflowStep'] = None


class WorkflowOrchestrator:
    """
    Orchestrates multi-step workflows with LLMs and custom functions.
    Supports sequential, parallel, and conditional execution.
    """
    
    def __init__(self, registry):
        """
        Initialize orchestrator with provider registry.
        
        Args:
            registry: ProviderRegistry instance
        """
        self.registry = registry
        self.workflows: Dict[str, List[WorkflowStep]] = {}
    
    def register_workflow(self, name: str, steps: List[WorkflowStep]) -> None:
        """
        Register a workflow.
        
        Args:
            name: Workflow name
            steps: List of workflow steps
        """
        self.workflows[name] = steps
        logger.info(f"Registered workflow: {name} ({len(steps)} steps)")
    
    async def execute_workflow(
        self,
        workflow_name: str,
        inputs: Dict[str, Any]
    ) -> WorkflowContext:
        """
        Execute a registered workflow.
        
        Args:
            workflow_name: Name of workflow to execute
            inputs: Input data for the workflow
            
        Returns:
            WorkflowContext with results
        """
        if workflow_name not in self.workflows:
            raise ValueError(f"Workflow '{workflow_name}' not registered")
        
        steps = self.workflows[workflow_name]
        context = WorkflowContext(inputs=inputs)
        metrics = get_metrics_collector()
        start_time = time.time()
        success = False
        
        try:
            logger.info(f"Executing workflow: {workflow_name}")
            
            for step in steps:
                await self._execute_step(step, context)
            
            success = True
            logger.info(f"Workflow completed: {workflow_name}")
            return context
            
        finally:
            # Record workflow execution metrics
            duration = time.time() - start_time
            metrics.record_workflow_execution(
                duration=duration,
                workflow_name=workflow_name,
                success=success
            )
    
    async def execute_steps(
        self,
        steps: List[WorkflowStep],
        inputs: Dict[str, Any]
    ) -> WorkflowContext:
        """
        Execute a list of steps (ad-hoc workflow).
        
        Args:
            steps: Steps to execute
            inputs: Input data
            
        Returns:
            WorkflowContext with results
        """
        context = WorkflowContext(inputs=inputs)
        
        for step in steps:
            await self._execute_step(step, context)
        
        return context
    
    async def _execute_step(
        self,
        step: WorkflowStep,
        context: WorkflowContext
    ) -> None:
        """Execute a single workflow step"""
        logger.info(f"Executing step: {step.name} (type={step.step_type.value})")
        
        try:
            if step.step_type == StepType.LLM:
                await self._execute_llm_step(step, context)
            
            elif step.step_type == StepType.FUNCTION:
                await self._execute_function_step(step, context)
            
            elif step.step_type == StepType.PARALLEL:
                await self._execute_parallel_step(step, context)
            
            elif step.step_type == StepType.CONDITIONAL:
                await self._execute_conditional_step(step, context)
            
            context.steps_executed.append(step.name)
            logger.info(f"Step completed: {step.name}")
            
        except Exception as e:
            logger.error(f"Step failed: {step.name} - {e}")
            raise
    
    async def _execute_llm_step(
        self,
        step: WorkflowStep,
        context: WorkflowContext
    ) -> None:
        """Execute an LLM generation step"""
        # Render prompt template with context variables
        prompt = self._render_template(step.prompt_template, context)
        
        # Filter out workflow-specific config keys
        workflow_keys = {'output_key', 'store_full_response'}
        llm_config = {
            k: v for k, v in step.config.items()
            if k not in workflow_keys
        }
        
        # Generate using registry
        response = await self.registry.generate(
            prompt=prompt,
            provider=step.provider,
            model=step.model,
            **llm_config
        )
        
        # Store result in context
        output_key = step.config.get('output_key', f"{step.name}_output")
        context.set(output_key, response.content)
        
        # Also store full response if requested
        if step.config.get('store_full_response', False):
            context.set(f"{output_key}_full", response)
    
    async def _execute_function_step(
        self,
        step: WorkflowStep,
        context: WorkflowContext
    ) -> None:
        """Execute a custom function step"""
        if step.function is None:
            raise ValueError(f"Step {step.name} has no function defined")
        
        # Call function with context
        if asyncio.iscoroutinefunction(step.function):
            result = await step.function(context)
        else:
            result = step.function(context)
        
        # Store result if returned
        if result is not None:
            output_key = step.config.get('output_key', f"{step.name}_output")
            context.set(output_key, result)
    
    async def _execute_parallel_step(
        self,
        step: WorkflowStep,
        context: WorkflowContext
    ) -> None:
        """Execute multiple steps in parallel"""
        if not step.parallel_steps:
            return
        
        # Execute all parallel steps
        tasks = [
            self._execute_step(parallel_step, context)
            for parallel_step in step.parallel_steps
        ]
        
        await asyncio.gather(*tasks)
    
    async def _execute_conditional_step(
        self,
        step: WorkflowStep,
        context: WorkflowContext
    ) -> None:
        """Execute conditional branching"""
        if step.condition is None:
            raise ValueError(f"Step {step.name} has no condition defined")
        
        # Evaluate condition
        condition_result = step.condition(context)
        
        # Execute appropriate branch
        if condition_result and step.if_true:
            await self._execute_step(step.if_true, context)
        elif not condition_result and step.if_false:
            await self._execute_step(step.if_false, context)
    
    def _render_template(
        self,
        template: str,
        context: WorkflowContext
    ) -> str:
        """
        Render a prompt template with context variables.
        Simple {variable} substitution.
        """
        if not template:
            return ""
        
        result = template
        
        # Replace {variable} with context values
        import re
        pattern = r'\{(\w+)\}'
        
        def replace_var(match):
            var_name = match.group(1)
            value = context.get(var_name, match.group(0))
            return str(value)
        
        result = re.sub(pattern, replace_var, result)
        return result
    
    def create_llm_step(
        self,
        name: str,
        prompt_template: str,
        output_key: Optional[str] = None,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        **kwargs
    ) -> WorkflowStep:
        """
        Convenience method to create an LLM step.
        
        Args:
            name: Step name
            prompt_template: Prompt with {variables}
            output_key: Key to store result (default: {name}_output)
            provider: Provider to use
            model: Model to use
            **kwargs: Additional generation parameters
        """
        config = kwargs.copy()
        if output_key:
            config['output_key'] = output_key
        
        return WorkflowStep(
            name=name,
            step_type=StepType.LLM,
            prompt_template=prompt_template,
            provider=provider,
            model=model,
            config=config
        )
    
    def create_function_step(
        self,
        name: str,
        function: Callable,
        output_key: Optional[str] = None,
        **kwargs
    ) -> WorkflowStep:
        """Convenience method to create a function step"""
        config = kwargs.copy()
        if output_key:
            config['output_key'] = output_key
        
        return WorkflowStep(
            name=name,
            step_type=StepType.FUNCTION,
            function=function,
            config=config
        )
