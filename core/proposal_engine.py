"""
Vectrax Proposal Engine
========================
Enables controlled self-evolution by analyzing proposed changes,
generating code, calculating risk, and presenting changes for review.

Integration:
  - Uses SmartRouter to route analysis and generation to appropriate models
  - Uses RiskEngine to assess risk of proposed changes
  - Uses Governor to check if changes are allowed in current mode
  - Presents detailed diff and risk assessment before any changes
"""

from __future__ import annotations

import difflib
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core import state_manager
from core.abstraction import GenerateRequest
from core.autonomy_policy import (
    Zone,
    ZONE_LABELS,
    classify_path,
    classify_proposal,
    can_auto_apply,
    load_autonomy_config,
)
from core.governor import get_current_policy
from core.providers import OllamaProvider
from core.risk_engine import OperationContext, RiskEngine, RiskLevel
from core.shadow_mode import compute_confidence


@dataclass
class FileChange:
    """Represents a proposed change to a file."""
    path: str
    action: str  # "create", "modify", "delete"
    original_content: Optional[str] = None
    proposed_content: Optional[str] = None
    description: str = ""
    zone: Optional[str] = None
    zone_label: Optional[str] = None
    
    def get_diff(self) -> str:
        """Generate unified diff for this change."""
        if self.action == "create":
            if not self.proposed_content:
                return f"Create empty file: {self.path}"
            lines = self.proposed_content.splitlines(keepends=True)
            return f"+++ {self.path}\n" + "".join(f"+{line}" for line in lines)
        
        elif self.action == "delete":
            if not self.original_content:
                return f"Delete file: {self.path}"
            lines = self.original_content.splitlines(keepends=True)
            return f"--- {self.path}\n" + "".join(f"-{line}" for line in lines)
        
        elif self.action == "modify":
            if not self.original_content or not self.proposed_content:
                return f"Modify file: {self.path} (no diff available)"
            
            orig_lines = self.original_content.splitlines(keepends=True)
            prop_lines = self.proposed_content.splitlines(keepends=True)
            
            diff = difflib.unified_diff(
                orig_lines,
                prop_lines,
                fromfile=f"a/{self.path}",
                tofile=f"b/{self.path}",
                lineterm=""
            )
            return "\n".join(diff)
        
        return f"Unknown action: {self.action}"


@dataclass
class Proposal:
    """Complete proposal for system changes."""
    description: str
    file_changes: List[FileChange] = field(default_factory=list)
    risk_score: Optional[float] = None
    risk_level: Optional[str] = None
    risk_breakdown: Optional[Dict[str, Any]] = None
    governor_policy: Optional[Dict[str, Any]] = None
    allowed: bool = False
    rationale: str = ""
    confidence_score: float = 0.0
    selected_provider: str = "ollama"
    selected_model: str = "unknown"
    autonomy_classification: Optional[Dict[str, Any]] = None
    auto_apply_allowed: bool = False
    auto_apply_reason: str = ""
    
    def summary(self) -> str:
        """Generate human-readable summary."""
        lines = [
            f"📋 Proposal: {self.description}",
            f"",
            f"Files affected: {len(self.file_changes)}",
        ]
        
        for change in self.file_changes:
            action_icon = {"create": "➕", "modify": "✏️", "delete": "❌"}.get(change.action, "?")
            lines.append(f"  {action_icon} {change.action.upper()}: {change.path}")
        
        lines.extend([
            f"",
            f"Risk Assessment:",
            f"  Score: {self.risk_score:.3f}" if self.risk_score is not None else "  Score: N/A",
            f"  Level: {self.risk_level}" if self.risk_level else "  Level: N/A",
            f"  Confidence: {self.confidence_score:.1%}",
            f"",
            f"Governor Status:",
            f"  Mode: {self.governor_policy.get('mode', 'unknown')}" if self.governor_policy else "  Mode: unknown",
            f"  Allowed: {'✅ Yes' if self.allowed else '❌ No'}",
        ])
        
        if not self.allowed:
            lines.append(f"  Reason: {self.rationale}")
        
        # Autonomy classification
        if self.autonomy_classification:
            ac = self.autonomy_classification
            lines.extend([
                f"",
                f"Autonomy Classification:",
                f"  Highest Zone: {ac.get('highest_zone_label', 'N/A')}",
                f"  Auto-Apply: {'✅ Permitido' if self.auto_apply_allowed else '❌ No permitido'}",
                f"  Reason: {self.auto_apply_reason}",
            ])
            for pf in ac.get("per_file", []):
                lines.append(f"    • {pf['path']}: {pf['zone_label']}")

        lines.extend([
            f"",
            f"Model Used: {self.selected_provider}/{self.selected_model}",
        ])
        
        return "\n".join(lines)
    
    def full_diff(self) -> str:
        """Generate complete diff for all changes."""
        if not self.file_changes:
            return "No file changes proposed."
        
        diffs = []
        for change in self.file_changes:
            diffs.append(f"\n{'='*70}")
            diffs.append(f"File: {change.path} ({change.action})")
            diffs.append(f"{'='*70}")
            if change.description:
                diffs.append(f"Description: {change.description}")
            diffs.append(change.get_diff())
        
        return "\n".join(diffs)


class ProposalEngine:
    """
    Engine for analyzing and generating system change proposals.
    
    Usage:
        engine = ProposalEngine()
        proposal = await engine.propose("Add logging to SmartRouter")
        print(proposal.summary())
        print(proposal.full_diff())
        
        if proposal.allowed:
            # User reviews and confirms
            await engine.apply(proposal)
    """
    
    def __init__(self, project_root: Optional[str] = None):
        """
        Initialize the proposal engine.
        
        Args:
            project_root: Root directory of the project (default: current dir)
        """
        self.project_root = Path(project_root or os.getcwd())
        self.risk_engine = RiskEngine()
        self.provider = OllamaProvider()
    
    async def propose(
        self,
        description: str,
        model: str = "qwen2.5-coder:7b"
    ) -> Proposal:
        """
        Generate a proposal for the described change.
        
        Args:
            description: Natural language description of the change
            model: Model to use for analysis and generation
            
        Returns:
            Proposal object with all details
        """
        # Check provider health
        if not await self.provider.health_check():
            raise RuntimeError("Ollama is not running. Start it with: brew services start ollama")
        
        # Get current governor policy
        policy = get_current_policy()
        
        # Step 1: Analyze what files need to be changed
        analysis = await self._analyze_changes(description, model)
        
        # Step 2: Generate code changes for each file
        file_changes = []
        for file_info in analysis.get("files", []):
            change = await self._generate_file_change(
                description,
                file_info,
                model
            )
            if change:
                file_changes.append(change)
        
        # Step 3: Calculate risk
        risk_ctx = OperationContext(
            op_type="autopatch",
            irreversibility_flags=self._get_irreversibility_flags(file_changes),
            exposure_flags=[],
            topic="code",
            governor_mode=policy.get("mode"),
        )
        
        risk_assessment = self.risk_engine.assess(risk_ctx)

        # Step 3.5: Compute confidence score from risk assessment
        conf_score = compute_confidence(risk_assessment)

        # Step 3.6: Classify files by autonomy zone
        file_paths = [c.path for c in file_changes]
        total_diff_lines = sum(
            len(c.get_diff().splitlines()) for c in file_changes
        )
        autonomy_class = classify_proposal(
            file_paths,
            risk_score=risk_assessment.risk_score,
            confidence_score=conf_score,
            diff_lines=total_diff_lines,
        )

        # Tag each FileChange with its zone
        for change in file_changes:
            zone = classify_path(change.path)
            change.zone = zone.value
            change.zone_label = ZONE_LABELS[zone]

        # Step 4: Check if changes are allowed
        allowed = policy.get("autopatch_allowed", False)
        rationale = policy.get("reason", "")
        
        # Block if risk is too high
        if risk_assessment.risk_level in (RiskLevel.HIGH, RiskLevel.CRITICAL):
            allowed = False
            rationale = f"Risk level {risk_assessment.risk_level.value} exceeds threshold. {rationale}"
        
        # Create proposal
        proposal = Proposal(
            description=description,
            file_changes=file_changes,
            risk_score=risk_assessment.risk_score,
            risk_level=risk_assessment.risk_level.value,
            risk_breakdown=risk_assessment.breakdown(),
            governor_policy=policy,
            allowed=allowed,
            rationale=rationale,
            confidence_score=conf_score,
            selected_provider="ollama",
            selected_model=model,
            autonomy_classification=autonomy_class,
            auto_apply_allowed=autonomy_class["auto_apply_allowed"],
            auto_apply_reason=autonomy_class["reason"],
        )
        
        return proposal
    
    async def _analyze_changes(
        self,
        description: str,
        model: str
    ) -> Dict[str, Any]:
        """
        Analyze what files need to be changed.
        
        Returns:
            Dict with:
                files: List of {path, action, reason}
                confidence: 0-1 confidence score
        """
        # Build file tree context
        file_tree = self._build_file_tree()
        
        prompt = f"""You are analyzing a proposed change to a codebase.

PROJECT STRUCTURE:
{file_tree}

PROPOSED CHANGE:
{description}

TASK:
Analyze which files would need to be modified, created, or deleted to implement this change.
Return ONLY a JSON object with this structure:
{{
  "files": [
    {{"path": "relative/path/to/file.py", "action": "modify", "reason": "why this file needs to change"}},
    {{"path": "new/file.py", "action": "create", "reason": "why this file needs to be created"}}
  ],
  "confidence": 0.85,
  "analysis": "brief explanation of the approach"
}}

Be precise and conservative. Only include files that definitely need to change.
Use relative paths from project root.
"""
        
        request = GenerateRequest(
            prompt=prompt,
            model=model,
            temperature=0.3,  # Lower temperature for more deterministic analysis
        )
        
        # Get full response
        full_response = ""
        async for chunk in self.provider.stream(request):
            full_response += chunk
        
        # Parse JSON from response
        try:
            # Extract JSON from markdown code blocks if present
            json_match = re.search(r'```json\s*(.*?)\s*```', full_response, re.DOTALL)
            if json_match:
                json_str = json_match.group(1)
            else:
                # Try to find JSON object in the text
                json_match = re.search(r'\{.*\}', full_response, re.DOTALL)
                if json_match:
                    json_str = json_match.group(0)
                else:
                    json_str = full_response
            
            result = json.loads(json_str)
            return result
        except json.JSONDecodeError:
            # Fallback to empty result
            return {"files": [], "confidence": 0.0, "analysis": "Failed to parse analysis"}
    
    async def _generate_file_change(
        self,
        description: str,
        file_info: Dict[str, str],
        model: str
    ) -> Optional[FileChange]:
        """
        Generate the actual code change for a specific file.
        
        Args:
            description: Overall change description
            file_info: Dict with path, action, reason
            model: Model to use
            
        Returns:
            FileChange object or None if generation fails
        """
        file_path = self.project_root / file_info["path"]
        action = file_info["action"]
        
        # Read original content if file exists
        original_content = None
        if file_path.exists():
            try:
                original_content = file_path.read_text()
            except Exception:
                pass
        
        # Generate new content
        if action == "delete":
            return FileChange(
                path=file_info["path"],
                action=action,
                original_content=original_content,
                proposed_content=None,
                description=file_info.get("reason", ""),
            )
        
        elif action in ("create", "modify"):
            prompt = self._build_generation_prompt(
                description,
                file_info,
                original_content
            )
            
            request = GenerateRequest(
                prompt=prompt,
                model=model,
                temperature=0.2,
            )
            
            # Get full response
            generated_code = ""
            async for chunk in self.provider.stream(request):
                generated_code += chunk
            
            # Extract code from markdown if present
            code_match = re.search(r'```(?:python|javascript|typescript|go|rust)?\s*(.*?)\s*```', generated_code, re.DOTALL)
            if code_match:
                generated_code = code_match.group(1)
            
            return FileChange(
                path=file_info["path"],
                action=action,
                original_content=original_content,
                proposed_content=generated_code.strip(),
                description=file_info.get("reason", ""),
            )
        
        return None
    
    def _build_generation_prompt(
        self,
        description: str,
        file_info: Dict[str, str],
        original_content: Optional[str]
    ) -> str:
        """Build prompt for code generation."""
        if file_info["action"] == "create":
            return f"""Create a new file: {file_info['path']}

PURPOSE:
{description}

REASON FOR THIS FILE:
{file_info.get('reason', '')}

Generate the complete file content. Return ONLY the code, no explanations.
"""
        else:  # modify
            return f"""Modify existing file: {file_info['path']}

CHANGE DESCRIPTION:
{description}

REASON FOR MODIFYING THIS FILE:
{file_info.get('reason', '')}

CURRENT FILE CONTENT:
{original_content or '(file does not exist)'}

Generate the COMPLETE modified file content. Return ONLY the code, no explanations.
Make minimal changes necessary to implement the described change.
"""
    
    def _build_file_tree(self, max_depth: int = 3) -> str:
        """Build a simplified file tree for context."""
        lines = []
        
        def walk(path: Path, depth: int, prefix: str = ""):
            if depth > max_depth:
                return
            
            try:
                items = sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name))
                for i, item in enumerate(items):
                    # Skip hidden files, venv, cache, etc.
                    if item.name.startswith('.') or item.name in ('__pycache__', 'node_modules', 'venv', '.venv'):
                        continue
                    
                    is_last = i == len(items) - 1
                    current_prefix = "└── " if is_last else "├── "
                    lines.append(f"{prefix}{current_prefix}{item.name}")
                    
                    if item.is_dir():
                        next_prefix = prefix + ("    " if is_last else "│   ")
                        walk(item, depth + 1, next_prefix)
            except PermissionError:
                pass
        
        lines.append(str(self.project_root))
        walk(self.project_root, 0)
        return "\n".join(lines[:100])  # Limit to 100 lines
    
    def _get_irreversibility_flags(self, changes: List[FileChange]) -> List[str]:
        """Determine irreversibility flags from file changes."""
        flags = []
        
        for change in changes:
            if change.action in ("modify", "delete"):
                flags.append("writes_files")
                flags.append("modifies_state")
                break
        
        return list(set(flags))
    
    async def apply(self, proposal: Proposal) -> Dict[str, Any]:
        """
        Apply the proposed changes to the filesystem.
        
        Args:
            proposal: Approved proposal to apply
            
        Returns:
            Dict with:
                success: bool
                applied: List of successfully applied changes
                failed: List of failed changes with errors
        """
        if not proposal.allowed:
            return {
                "success": False,
                "error": f"Proposal not allowed: {proposal.rationale}",
                "applied": [],
                "failed": [],
            }
        
        applied = []
        failed = []
        
        for change in proposal.file_changes:
            try:
                file_path = self.project_root / change.path
                
                if change.action == "create":
                    file_path.parent.mkdir(parents=True, exist_ok=True)
                    file_path.write_text(change.proposed_content or "")
                    applied.append(change.path)
                
                elif change.action == "modify":
                    file_path.write_text(change.proposed_content or "")
                    applied.append(change.path)
                
                elif change.action == "delete":
                    if file_path.exists():
                        file_path.unlink()
                    applied.append(change.path)
            
            except Exception as e:
                failed.append({
                    "path": change.path,
                    "error": str(e)
                })
        
        # Record in state
        state_manager.increment("proposals_applied")
        
        return {
            "success": len(failed) == 0,
            "applied": applied,
            "failed": failed,
        }
    
    async def close(self):
        """Close the provider connection."""
        await self.provider.close()
