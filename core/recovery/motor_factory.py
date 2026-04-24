"""
Vectrax — /motor_factory
==========================
Fábrica de motores. Capa 3.

synthesize_spec(anomaly, signature, historical_context, constitutional_rules)
    → MotorSpec declarativo que describe la defensa estructural a construir.

generate_code(spec)
    → CandidateMotor con artifacts listos para validación.

Los specs NUNCA son point_fix — la constitución los rechaza.
Cada mechanism del spec se traduce en un artifact concreto.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List

from core.recovery.anomaly import Anomaly
from core.recovery.classifier import ErrorSignature


@dataclass
class MotorSpec:
    error_class: str
    mechanisms: List[str]
    scope: str = "structural"
    files_to_create: List[str] = field(default_factory=list)
    files_to_modify: List[str] = field(default_factory=list)
    tests: List[str] = field(default_factory=list)
    rationale: str = ""


@dataclass
class CandidateMotor:
    spec: MotorSpec
    artifacts: Dict[str, str] = field(default_factory=dict)   # path -> contenido


# Patrones seed — cada par (especie, mecanismo) tiene una receta estructural.
# Crece con cada clase absorbida.
_RECIPES = {
    ("interface", "duplication"): {
        "mechanisms": ["unifier", "detector", "guardian", "refactor"],
        "rationale": (
            "Duplicación en superficies del gateway → capa unificada + "
            "detector runtime + hook pre-commit + refactor de surface."
        ),
    },
    ("routing", "missing_guard"): {
        "mechanisms": ["detector", "rollback", "guardian"],
        "rationale": (
            "Ruta sin guard → detector end-to-end + rollback automático + "
            "hook de config."
        ),
    },
    ("concurrency", "race"): {
        "mechanisms": ["detector", "rollback", "guardian", "test"],
        "rationale": (
            "Race condition → detector de proceso en D-state + rollback por "
            "restart limpio + test de carga."
        ),
    },
    ("integration", "drift"): {
        "mechanisms": ["detector", "guardian", "rollback"],
        "rationale": (
            "Drift de integración externa → detector de hash/expiry + guard "
            "de renovación + rollback a versión estable."
        ),
    },
    ("config", "drift"): {
        "mechanisms": ["detector", "guardian"],
        "rationale": (
            "Drift de config → detector de diff vs baseline + guardian pre-deploy."
        ),
    },
}


def synthesize_spec(
    anomaly: Anomaly,
    signature: ErrorSignature,
    historical_context: List[Dict[str, Any]] | None = None,
    constitutional_rules: List[str] | None = None,
) -> MotorSpec:
    """Capa 3a — blueprint."""
    key = (signature.species, signature.mechanism)
    recipe = _RECIPES.get(key)

    if recipe:
        mechanisms = list(recipe["mechanisms"])
        rationale = recipe["rationale"]
    else:
        # Especie nueva: motor embrionario con las defensas universales.
        # La constitución exige estructura, no parche — damos el mínimo.
        mechanisms = ["detector", "rollback", "guardian"]
        rationale = (
            f"Especie desconocida ({signature.species}/{signature.mechanism}) — "
            "motor embrionario con defensas universales; el aprendizaje lo "
            "expande."
        )

    surfaces = list((anomaly.evidence or {}).get("surfaces", []))

    files_create = []
    if "unifier" in mechanisms:
        files_create.append(f"vectrax/{signature.species}_unified_handler.py")
    if "detector" in mechanisms:
        files_create.append(
            f"core/recovery/detectors/{signature.species}_runtime.py"
        )
    if "guardian" in mechanisms:
        files_create.append(
            f"scripts/check_no_{signature.species}_regression.sh"
        )

    tests = [f"tests/test_{signature.species}_{signature.mechanism}.py"]

    return MotorSpec(
        error_class=signature.species,
        mechanisms=mechanisms,
        scope="structural",
        files_to_create=files_create,
        files_to_modify=surfaces,
        tests=tests,
        rationale=rationale,
    )


def generate_code(spec: MotorSpec) -> CandidateMotor:
    """
    Capa 3b — materialización.

    Estado actual: la generación de código real se hace con revisión humana
    (línea por línea) antes de instalar. Esta función deja preparados los
    placeholders; las siguientes iteraciones del motor de validación
    habilitarán generación plantilla + LLM con validator duro.
    """
    artifacts: Dict[str, str] = {}
    for path in spec.files_to_create:
        artifacts[path] = _skeleton_for(path, spec)
    return CandidateMotor(spec=spec, artifacts=artifacts)


def _skeleton_for(path: str, spec: MotorSpec) -> str:
    header = (
        f'"""\nAuto-generado por motor_factory.\n'
        f'error_class: {spec.error_class}\n'
        f'mechanisms: {spec.mechanisms}\n'
        f'rationale: {spec.rationale}\n'
        '"""\n'
    )
    if path.endswith(".sh"):
        return "#!/usr/bin/env bash\nset -e\n# Guardian stub — expand.\necho OK\n"
    return header + "# stub — pendiente revisión humana y expansión.\n"
