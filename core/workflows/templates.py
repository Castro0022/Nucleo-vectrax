"""
Pre-built Workflow Templates
Common patterns ready to use
"""
from .orchestrator import WorkflowOrchestrator, WorkflowStep, StepType


def create_multi_step_reasoning_workflow(orchestrator: WorkflowOrchestrator) -> str:
    """
    Multi-step reasoning: Plan → Execute → Validate
    
    Input: {"task": "description of task"}
    Output: {"plan", "execution", "validation", "final_result"}
    """
    steps = [
        # Step 1: Create a plan
        orchestrator.create_llm_step(
            name="planner",
            prompt_template="""Create a detailed step-by-step plan to accomplish this task:
            
Task: {task}

Provide a numbered list of specific steps.""",
            output_key="plan",
            model="llama3.2:3b",
            temperature=0.7
        ),
        
        # Step 2: Execute the plan
        orchestrator.create_llm_step(
            name="executor",
            prompt_template="""Execute this plan and provide the results:

Plan:
{plan}

Original task: {task}

Provide detailed execution results.""",
            output_key="execution",
            model="llama3.2:3b",
            temperature=0.5
        ),
        
        # Step 3: Validate results
        orchestrator.create_llm_step(
            name="validator",
            prompt_template="""Validate these execution results against the original task:

Task: {task}
Results: {execution}

Is the task completed successfully? Provide assessment and final answer.""",
            output_key="validation",
            model="llama3.2:3b",
            temperature=0.3
        ),
        
        # Step 4: Extract final result
        orchestrator.create_function_step(
            name="extract_final",
            function=lambda ctx: {
                "plan": ctx.get("plan"),
                "execution": ctx.get("execution"),
                "validation": ctx.get("validation"),
                "steps_executed": ctx.steps_executed
            },
            output_key="final_result"
        )
    ]
    
    workflow_name = "multi_step_reasoning"
    orchestrator.register_workflow(workflow_name, steps)
    return workflow_name


def create_code_generation_workflow(orchestrator: WorkflowOrchestrator) -> str:
    """
    Code generation: Spec → Code → Review → Refine
    
    Input: {"specification": "what to build"}
    Output: {"initial_code", "review", "final_code"}
    """
    steps = [
        # Step 1: Generate initial code
        orchestrator.create_llm_step(
            name="generate_code",
            prompt_template="""Write Python code for the following specification:

{specification}

Provide clean, well-documented code with docstrings.""",
            output_key="initial_code",
            model="qwen2.5-coder:7b",
            temperature=0.3
        ),
        
        # Step 2: Review the code
        orchestrator.create_llm_step(
            name="review_code",
            prompt_template="""Review this code and identify any issues:

{initial_code}

Specification: {specification}

Provide: 
1. Issues found (bugs, style, performance)
2. Suggestions for improvement""",
            output_key="review",
            model="qwen2.5-coder:7b",
            temperature=0.2
        ),
        
        # Step 3: Refine based on review
        orchestrator.create_llm_step(
            name="refine_code",
            prompt_template="""Improve this code based on the review:

Original code:
{initial_code}

Review feedback:
{review}

Provide the improved version.""",
            output_key="final_code",
            model="qwen2.5-coder:7b",
            temperature=0.3
        )
    ]
    
    workflow_name = "code_generation"
    orchestrator.register_workflow(workflow_name, steps)
    return workflow_name


def create_summarize_and_qa_workflow(orchestrator: WorkflowOrchestrator) -> str:
    """
    Summarize document and answer questions
    
    Input: {"document": "text", "question": "question"}
    Output: {"summary", "answer"}
    """
    steps = [
        # Step 1: Summarize document
        orchestrator.create_llm_step(
            name="summarize",
            prompt_template="""Summarize this document in 3-5 key points:

{document}

Provide a concise summary.""",
            output_key="summary",
            model="llama3.2:3b",
            temperature=0.3
        ),
        
        # Step 2: Answer question based on summary and document
        orchestrator.create_llm_step(
            name="answer_question",
            prompt_template="""Answer this question based on the document and summary:

Question: {question}

Summary: {summary}

Full document: {document}

Provide a clear, concise answer.""",
            output_key="answer",
            model="llama3.2:3b",
            temperature=0.5
        )
    ]
    
    workflow_name = "summarize_and_qa"
    orchestrator.register_workflow(workflow_name, steps)
    return workflow_name


def create_parallel_analysis_workflow(orchestrator: WorkflowOrchestrator) -> str:
    """
    Parallel analysis: Analyze text from multiple angles simultaneously
    
    Input: {"text": "text to analyze"}
    Output: {"sentiment", "entities", "summary", "synthesis"}
    """
    # Create parallel steps
    parallel_steps = [
        orchestrator.create_llm_step(
            name="sentiment_analysis",
            prompt_template="Analyze the sentiment of this text:\n\n{text}\n\nProvide: Positive, Negative, or Neutral with brief explanation.",
            output_key="sentiment",
            model="llama3.2:3b"
        ),
        orchestrator.create_llm_step(
            name="entity_extraction",
            prompt_template="Extract key entities (people, places, organizations) from this text:\n\n{text}\n\nList all important entities.",
            output_key="entities",
            model="llama3.2:3b"
        ),
        orchestrator.create_llm_step(
            name="key_points",
            prompt_template="Extract the 3 most important points from this text:\n\n{text}\n\nProvide numbered list.",
            output_key="summary",
            model="llama3.2:3b"
        )
    ]
    
    steps = [
        # Run analyses in parallel
        WorkflowStep(
            name="parallel_analysis",
            step_type=StepType.PARALLEL,
            parallel_steps=parallel_steps
        ),
        
        # Synthesize results
        orchestrator.create_llm_step(
            name="synthesize",
            prompt_template="""Synthesize these analyses into a comprehensive understanding:

Sentiment: {sentiment}
Entities: {entities}
Key Points: {summary}

Original text: {text}

Provide a cohesive synthesis.""",
            output_key="synthesis",
            model="llama3.2:3b"
        )
    ]
    
    workflow_name = "parallel_analysis"
    orchestrator.register_workflow(workflow_name, steps)
    return workflow_name


def create_iterative_refinement_workflow(orchestrator: WorkflowOrchestrator, iterations: int = 2) -> str:
    """
    Iterative refinement: Generate → Critique → Improve (repeat)
    
    Input: {"prompt": "what to generate"}
    Output: {"iteration_0", "iteration_1", ..., "final"}
    """
    steps = []
    
    # Initial generation
    steps.append(
        orchestrator.create_llm_step(
            name="initial_generation",
            prompt_template="{prompt}",
            output_key="iteration_0",
            model="llama3.2:3b"
        )
    )
    
    # Refinement iterations
    for i in range(iterations):
        prev_key = f"iteration_{i}"
        critique_key = f"critique_{i}"
        next_key = f"iteration_{i+1}"
        
        # Critique
        steps.append(
            orchestrator.create_llm_step(
                name=f"critique_{i}",
                prompt_template=f"Critique this output and suggest specific improvements:\n\n{{{prev_key}}}\n\nOriginal prompt: {{prompt}}\n\nProvide constructive feedback.",
                output_key=critique_key,
                model="llama3.2:3b"
            )
        )
        
        # Improve
        steps.append(
            orchestrator.create_llm_step(
                name=f"improve_{i}",
                prompt_template=f"Improve this based on the critique:\n\nOriginal: {{{prev_key}}}\n\nCritique: {{{critique_key}}}\n\nProvide improved version.",
                output_key=next_key,
                model="llama3.2:3b"
            )
        )
    
    # Mark final
    steps.append(
        orchestrator.create_function_step(
            name="mark_final",
            function=lambda ctx: ctx.get(f"iteration_{iterations}"),
            output_key="final"
        )
    )
    
    workflow_name = f"iterative_refinement_{iterations}"
    orchestrator.register_workflow(workflow_name, steps)
    return workflow_name


# Registry of all templates
WORKFLOW_TEMPLATES = {
    "multi_step_reasoning": create_multi_step_reasoning_workflow,
    "code_generation": create_code_generation_workflow,
    "summarize_and_qa": create_summarize_and_qa_workflow,
    "parallel_analysis": create_parallel_analysis_workflow,
    "iterative_refinement": create_iterative_refinement_workflow,
}


def register_all_templates(orchestrator: WorkflowOrchestrator) -> dict[str, str]:
    """
    Register all pre-built workflow templates.
    
    Returns:
        Dict mapping template name to workflow name
    """
    registered = {}
    for template_name, create_func in WORKFLOW_TEMPLATES.items():
        try:
            workflow_name = create_func(orchestrator)
            registered[template_name] = workflow_name
        except Exception as e:
            print(f"Failed to register {template_name}: {e}")
    
    return registered
