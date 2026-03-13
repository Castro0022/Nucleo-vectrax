# vx propose - Controlled Self-Evolution

## Overview

The `vx propose` command enables **controlled self-evolution** of the Vectrax system. It allows you to describe a change in natural language, and the system will:

1. **Analyze** which files need to be modified
2. **Generate** the necessary code changes
3. **Calculate** the risk level of the changes
4. **Show** a detailed diff of all proposed changes
5. **Wait** for explicit user confirmation before applying anything

This enables autonomous system improvement while maintaining human oversight and safety.

## Architecture Integration

The propose mode integrates with Vectrax's existing safety infrastructure:

```
User Input (Description)
         ↓
   ProposalEngine
         ↓
    ┌────────────┐
    │ SmartRouter │ → Routes to appropriate model (default: qwen2.5-coder:7b)
    └────────────┘
         ↓
   Analysis Phase
    - Analyze file tree
    - Determine affected files
    - Generate code for each file
         ↓
   ┌──────────────┐
   │  RiskEngine  │ → Calculate risk score (6 signals)
   └──────────────┘
         ↓
   ┌──────────────┐
   │   Governor   │ → Check if changes are allowed in current mode
   └──────────────┘
         ↓
   Present to User
    - Summary
    - Risk breakdown
    - Full diff
    - Confirmation prompt
         ↓
   Apply (if confirmed)
```

## Usage

### Basic Usage

```bash
vx propose "Add logging to SmartRouter class"
```

### With Custom Model

```bash
vx propose "Create a new rate limiter for API calls" --model llama3.2:3b
```

### Example Session

```bash
$ vx propose "Add a comment to the load function in core/state_manager.py"

🔍 Analyzing proposal: Add a comment to the load function in core/state_manager.py

⏳ Generating proposal (this may take a moment)...

📋 Proposal: Add a comment to the load function in core/state_manager.py

Files affected: 1
  ✏️ MODIFY: core/state_manager.py

Risk Assessment:
  Score: 0.470
  Level: MEDIUM
  Confidence: 85.0%

Governor Status:
  Mode: act
  Allowed: ✅ Yes

Model Used: ollama/qwen2.5-coder:7b

======================================================================

📊 Risk Breakdown:
  • impact: 0.500 (weight: 0.250, contribution: 0.125)
    └─ op_type=autopatch
  • irreversibility: 0.800 (weight: 0.200, contribution: 0.160)
    └─ flags=['writes_files', 'modifies_state']
  • exposure: 0.000 (weight: 0.150, contribution: 0.000)
    └─ flags=[]
  • sensitivity: 0.300 (weight: 0.150, contribution: 0.045)
    └─ topic=code
  • runtime: 0.000 (weight: 0.150, contribution: 0.000)
    └─ governor_mode=act
  • drift: 0.000 (weight: 0.100, contribution: 0.000)
    └─ latency+error_rate EMA drift

======================================================================

📝 Proposed Changes:

======================================================================
File: core/state_manager.py (modify)
======================================================================
Description: Add comment explaining the load function

--- a/core/state_manager.py
+++ b/core/state_manager.py
@@ -44,6 +44,10 @@
 
 
 def load():
-    """Load cognitive state from disk. Returns default if missing/corrupt."""
+    """
+    Load cognitive state from disk.
+    
+    Returns default if missing/corrupt.
+    """
     if not os.path.isfile(STATE_PATH):
         return dict(DEFAULT_STATE)

======================================================================

✅ This proposal is allowed by the Governor.

⚠️  IMPORTANT: These changes have NOT been applied yet.
   Review the changes carefully before proceeding.

Apply these changes? (yes/no): no

❌ Changes NOT applied. Proposal discarded.
```

## Risk Assessment

The propose mode uses the RiskEngine to calculate a comprehensive risk score based on 6 signals:

### 1. Impact (25% weight)
- **autopatch**: 0.9 (high impact - modifies system behavior)
- **workflow**: 0.7
- **llm_generate**: 0.5
- **read_only**: 0.0

### 2. Irreversibility (20% weight)
Flags like `writes_files`, `modifies_state` increase this score.

### 3. External Exposure (15% weight)
Checked for cloud APIs, network calls, etc.

### 4. Sensitivity (15% weight)
Based on the topic (code=0.3, health=0.9, trading=0.8)

### 5. Runtime Health (15% weight)
- Current error rate
- System latency
- Circuit breaker status
- Governor mode

### 6. Drift from Baseline (10% weight)
Deviation from historical averages

### Risk Levels

- **LOW** (< 0.30): Safe to proceed
- **MEDIUM** (0.30-0.60): Proceed with caution
- **HIGH** (0.60-0.85): Requires careful review
- **CRITICAL** (≥ 0.85): Blocked automatically unless in `act` mode

## Governor Integration

The Governor controls whether proposals are allowed based on system health:

### Governor Modes

1. **observe** - First 3 cycles after boot; proposals blocked
2. **act** - Healthy system; all proposals allowed
3. **cautious** - Minor issues; proposals allowed with warnings
4. **recover** - Multiple errors; proposals blocked

### Mode Transitions

```
Boot → observe (3 cycles)
     ↓
   act (when healthy)
     ↓
   cautious (on errors)
     ↓
   recover (≥2 errors in 10 cycles)
     ↓
   cautious (3 consecutive clean cycles)
```

## Safety Features

### 1. No Automatic Application
Changes are **never** applied automatically. User must explicitly confirm.

### 2. Full Transparency
- Complete diff shown before confirmation
- Risk breakdown with all signals
- Governor policy clearly displayed

### 3. Risk-Based Blocking
High and critical risk proposals are blocked even if Governor allows them.

### 4. State Tracking
All applied proposals are recorded in `~/.vectrax/cognition_state.json`:

```json
{
  "proposals_applied": 5
}
```

## File Change Actions

The propose mode supports three types of file changes:

### CREATE
Create a new file with generated content.

### MODIFY
Modify an existing file. Shows unified diff.

### DELETE
Delete a file (rarely used, high risk).

## Model Selection

By default, propose uses `qwen2.5-coder:7b` for code-related changes because it's optimized for code generation.

You can override with `--model`:

```bash
vx propose "..." --model llama3.2:3b
```

## Limitations

1. **Context Size**: Large codebases may exceed model context window
2. **Analysis Accuracy**: Depends on model's understanding of the codebase
3. **Determinism**: Results may vary between runs due to LLM non-determinism
4. **No Rollback**: Applied changes must be manually reverted if issues arise

## Best Practices

### ✅ Do

- Use descriptive, specific change descriptions
- Review diffs carefully before confirming
- Test changes after applying them
- Use version control to track changes
- Start with small, focused changes

### ❌ Don't

- Blindly accept all proposals
- Make multiple large changes at once
- Use in production without testing
- Ignore risk warnings
- Apply proposals you don't understand

## Examples

### Add Functionality
```bash
vx propose "Add a retry decorator to handle transient failures"
```

### Refactor
```bash
vx propose "Extract the file tree building logic into a separate method"
```

### Documentation
```bash
vx propose "Add docstrings to all functions in core/governor.py"
```

### Testing
```bash
vx propose "Create unit tests for the RiskEngine class"
```

### Bug Fixes
```bash
vx propose "Fix the off-by-one error in error_history window calculation"
```

## Integration with CI/CD

The propose mode can be integrated into development workflows:

```bash
# Review proposal without applying
vx propose "Add health check endpoint" > proposal.txt
git add proposal.txt
git commit -m "Proposal: Add health check endpoint"

# Later, after review
vx propose "Add health check endpoint"
# User reviews and confirms
```

## Future Enhancements

Potential improvements:

1. **Dry-run mode**: Show changes without LLM calls
2. **Proposal history**: Track all proposals (applied and rejected)
3. **Rollback support**: Automatic undo of applied changes
4. **Batch proposals**: Apply multiple related changes together
5. **Smart retry**: Regenerate proposals that fail validation
6. **Integration tests**: Run tests automatically before applying

## Troubleshooting

### "Ollama is not running"
```bash
brew services start ollama
```

### "Proposal not allowed"
Check Governor mode:
```bash
vx status
```

### "Failed to parse analysis"
The model may have returned invalid JSON. Try:
- Using a different model with `--model`
- Simplifying the description
- Breaking into smaller changes

### Empty file changes
The model may not have understood the request. Be more specific:
- ❌ "improve the code"
- ✅ "add error handling to the propose function"

## Related Documentation

- [RiskEngine Documentation](RISK_ENGINE.md)
- [Governor Documentation](GOVERNOR.md)
- [SmartRouter Documentation](SMART_ROUTER.md)
- [Phase 6: Hardening](PHASE6_COMPLETE.md)

## Contributing

To extend the propose mode:

1. Add new risk signals to `core/risk_engine.py`
2. Enhance file analysis in `ProposalEngine._analyze_changes()`
3. Improve code generation prompts
4. Add support for new file types
5. Integrate with additional safety checks

---

**Made with 🤖 by the Vectrax community**

*Autonomous. Safe. Controlled.*
