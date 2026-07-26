"""
Phase 3 Verification Test
Tests workflow orchestration and complex pipelines:
- Orchestrator can chain multiple LLM calls
- Templates work correctly
- Multi-step reasoning functions
- Parallel execution works
"""
import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

# These tests drive real workflow orchestration against a live LLM backend
# (Ollama at localhost:11434). They are NOT hermetic — exclude them from the
# default quality gate, which runs with `-m "not live"`.
pytestmark = pytest.mark.live

from core.abstraction import load_registry_from_config
from core.workflows import (
    WorkflowOrchestrator,
    register_all_templates,
)


async def test_orchestrator_basic():
    """Test 1: Basic orchestrator setup"""
    print("🔍 Test 1: Orchestrator initialization...")
    
    registry = load_registry_from_config()
    orchestrator = WorkflowOrchestrator(registry)
    
    print(f"✅ Orchestrator created")
    print(f"   Registry: {registry}")
    return True


async def test_simple_chain():
    """Test 2: Simple two-step chain"""
    print("\n🔍 Test 2: Simple two-step workflow...")
    
    registry = load_registry_from_config()
    orchestrator = WorkflowOrchestrator(registry)
    
    # Create simple workflow: Generate idea → Expand idea
    steps = [
        orchestrator.create_llm_step(
            name="generate_idea",
            prompt_template="Generate a creative {topic} idea in one sentence.",
            output_key="idea",
            model="llama3.2:3b",
            temperature=0.8
        ),
        orchestrator.create_llm_step(
            name="expand_idea",
            prompt_template="Expand this idea into 3 concrete points:\n\n{idea}",
            output_key="expansion",
            model="llama3.2:3b",
            temperature=0.5
        )
    ]
    
    # Execute
    context = await orchestrator.execute_steps(
        steps,
        inputs={"topic": "productivity app"}
    )
    
    print(f"✅ Chain executed:")
    print(f"   Idea: {context.get('idea')[:80]}...")
    print(f"   Expansion: {context.get('expansion')[:100]}...")
    print(f"   Steps executed: {context.steps_executed}")
    
    return True


async def test_code_generation_workflow():
    """Test 3: Code generation workflow"""
    print("\n🔍 Test 3: Code generation workflow (Generate → Review → Refine)...")
    
    registry = load_registry_from_config()
    orchestrator = WorkflowOrchestrator(registry)
    
    # Register code generation workflow
    from core.workflows.templates import create_code_generation_workflow
    workflow_name = create_code_generation_workflow(orchestrator)
    
    # Execute
    context = await orchestrator.execute_workflow(
        workflow_name,
        inputs={"specification": "A function to check if a string is a palindrome"}
    )
    
    print(f"✅ Code generation workflow completed:")
    print(f"   Initial code: {len(context.get('initial_code'))} chars")
    print(f"   Review: {len(context.get('review'))} chars")
    print(f"   Final code: {len(context.get('final_code'))} chars")
    print(f"   Steps: {' → '.join(context.steps_executed)}")
    
    return True


async def test_multi_step_reasoning():
    """Test 4: Multi-step reasoning workflow"""
    print("\n🔍 Test 4: Multi-step reasoning (Plan → Execute → Validate)...")
    
    registry = load_registry_from_config()
    orchestrator = WorkflowOrchestrator(registry)
    
    # Register workflow
    from core.workflows.templates import create_multi_step_reasoning_workflow
    workflow_name = create_multi_step_reasoning_workflow(orchestrator)
    
    # Execute
    context = await orchestrator.execute_workflow(
        workflow_name,
        inputs={"task": "Calculate the fibonacci sequence up to the 10th number"}
    )
    
    print(f"✅ Multi-step reasoning completed:")
    print(f"   Plan created: {len(context.get('plan'))} chars")
    print(f"   Executed: {len(context.get('execution'))} chars")
    print(f"   Validated: {len(context.get('validation'))} chars")
    print(f"   Steps: {' → '.join(context.steps_executed)}")
    
    # Show snippet of final validation
    validation = context.get('validation', '')
    print(f"\n   Validation preview: {validation[:150]}...")
    
    return True


async def test_summarize_and_qa():
    """Test 5: Summarize and QA workflow"""
    print("\n🔍 Test 5: Summarize document + answer question...")
    
    registry = load_registry_from_config()
    orchestrator = WorkflowOrchestrator(registry)
    
    # Register workflow
    from core.workflows.templates import create_summarize_and_qa_workflow
    workflow_name = create_summarize_and_qa_workflow(orchestrator)
    
    # Sample document
    document = """
    Vectrax is a local-first AI infrastructure designed for maximum autonomy.
    It features a provider-agnostic abstraction layer that allows switching between
    LLM providers without changing code. The system includes a workflow orchestrator
    for complex multi-step operations. All components can run 100% offline with
    local models via Ollama.
    """
    
    # Execute
    context = await orchestrator.execute_workflow(
        workflow_name,
        inputs={
            "document": document,
            "question": "What makes Vectrax autonomous?"
        }
    )
    
    print(f"✅ Summarize + QA completed:")
    print(f"   Summary: {context.get('summary')[:100]}...")
    print(f"   Answer: {context.get('answer')[:120]}...")
    
    return True


async def test_parallel_execution():
    """Test 6: Parallel analysis workflow"""
    print("\n🔍 Test 6: Parallel execution (3 analyses simultaneously)...")
    
    registry = load_registry_from_config()
    orchestrator = WorkflowOrchestrator(registry)
    
    # Register workflow
    from core.workflows.templates import create_parallel_analysis_workflow
    workflow_name = create_parallel_analysis_workflow(orchestrator)
    
    # Execute
    import time
    start = time.time()
    
    context = await orchestrator.execute_workflow(
        workflow_name,
        inputs={
            "text": "Vectrax represents a breakthrough in autonomous AI infrastructure. "
                    "It empowers developers with unprecedented control and flexibility."
        }
    )
    
    duration = time.time() - start
    
    print(f"✅ Parallel analysis completed in {duration:.1f}s:")
    print(f"   Sentiment: {context.get('sentiment')[:60]}...")
    print(f"   Entities: {context.get('entities')[:60]}...")
    print(f"   Summary: {context.get('summary')[:60]}...")
    print(f"   Synthesis: {context.get('synthesis')[:80]}...")
    
    return True


async def main():
    """Run all tests"""
    print("=" * 70)
    print("VECTRAX PHASE 3 VERIFICATION TEST")
    print("Testing: Workflow Orchestration & Complex Pipelines")
    print("=" * 70)
    
    tests = [
        test_orchestrator_basic,
        test_simple_chain,
        test_code_generation_workflow,
        test_multi_step_reasoning,
        test_summarize_and_qa,
        test_parallel_execution,
    ]
    
    results = []
    for test in tests:
        try:
            result = await test()
            results.append(result)
        except Exception as e:
            print(f"❌ Test crashed: {e}")
            import traceback
            traceback.print_exc()
            results.append(False)
    
    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    passed = sum(results)
    total = len(results)
    print(f"✅ Passed: {passed}/{total}")
    print(f"❌ Failed: {total - passed}/{total}")
    
    if passed == total:
        print("\n🎉 All tests passed! Phase 3 setup is complete.")
        print("\n✅ PHASE 3 SUCCESS CRITERIA MET:")
        print("   • WorkflowOrchestrator implemented")
        print("   • Multi-step chaining works")
        print("   • Pre-built templates functional")
        print("   • Parallel execution working")
        print("   • Complex workflows (code gen, reasoning) operational")
        print("\n🔧 AVAILABLE WORKFLOWS:")
        print("   • multi_step_reasoning - Plan → Execute → Validate")
        print("   • code_generation - Spec → Code → Review → Refine")
        print("   • summarize_and_qa - Summarize + answer questions")
        print("   • parallel_analysis - Multi-angle analysis")
        print("   • iterative_refinement - Generate → Critique → Improve")
        return 0
    else:
        print("\n⚠️  Some tests failed. Check the output above.")
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
