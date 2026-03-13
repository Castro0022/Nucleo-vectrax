"""
Vectrax Core — Contracts Reader
===================================
Puente entre el sistema de contratos de conectores (connectors.contracts)
y el operador universal. Descubre, valida, analiza y resume contratos
registrados en el UCE.

Principio P-001: "No actúes sin comprender."
Antes de usar un conector, el operador debe comprender su contrato.

Capa: 10 — Sistema de Contratos
Creado: 2026-03-10
Creador: Mario Bravo Castro
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from connectors.contracts.schema import ConnectorContract
from connectors.contracts.validator import validate_contract
from connectors.contracts.families import ConnectorFamily
from connectors.core.types import ConnectorCapability

logger = logging.getLogger("vectrax.operator.contracts_reader")


# ---------------------------------------------------------------------------
# Nivel de riesgo de un contrato
# ---------------------------------------------------------------------------

class ContractRiskLevel(str, Enum):
    """Nivel de riesgo inferido del contrato de un conector."""
    LOW = "low"           # Solo lectura, sin credenciales sensibles
    MEDIUM = "medium"     # Escritura o autenticación con token
    HIGH = "high"         # Ejecución, credenciales críticas, o sin fallback
    CRITICAL = "critical" # Ejecución + riesgos declarados + sin logs


# ---------------------------------------------------------------------------
# Resultado de análisis
# ---------------------------------------------------------------------------

@dataclass
class ContractAnalysis:
    """Resultado del análisis de un contrato individual."""
    name: str
    family: str
    version: str
    valid: bool
    violations: List[str] = field(default_factory=list)
    risk_level: ContractRiskLevel = ContractRiskLevel.LOW
    risk_reasons: List[str] = field(default_factory=list)
    capabilities: List[str] = field(default_factory=list)
    has_fallback: bool = False
    logs_enabled: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "family": self.family,
            "version": self.version,
            "valid": self.valid,
            "violations": self.violations,
            "risk_level": self.risk_level.value,
            "risk_reasons": self.risk_reasons,
            "capabilities": self.capabilities,
            "has_fallback": self.has_fallback,
            "logs_enabled": self.logs_enabled,
        }


@dataclass
class ContractsSummary:
    """Resumen global del estado de contratos del sistema."""
    total: int = 0
    valid: int = 0
    invalid: int = 0
    by_family: Dict[str, int] = field(default_factory=dict)
    by_risk: Dict[str, int] = field(default_factory=dict)
    analyses: List[ContractAnalysis] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total": self.total,
            "valid": self.valid,
            "invalid": self.invalid,
            "by_family": self.by_family,
            "by_risk": self.by_risk,
            "analyses": [a.to_dict() for a in self.analyses],
        }


# ---------------------------------------------------------------------------
# Motor de análisis de riesgo
# ---------------------------------------------------------------------------

_HIGH_RISK_CAPABILITIES = {ConnectorCapability.EXECUTE, ConnectorCapability.WRITE}


def _assess_risk(contract: ConnectorContract) -> Tuple[ContractRiskLevel, List[str]]:
    """
    Evalúa el nivel de riesgo de un contrato basado en:
    - Capacidades declaradas (EXECUTE, WRITE)
    - Riesgos declarados por el autor
    - Presencia de fallback
    - Estado de logging
    - Método de autenticación
    """
    reasons: List[str] = []
    score = 0

    # Capacidades de alto riesgo
    caps = set(contract.capabilities)
    if ConnectorCapability.EXECUTE in caps:
        score += 3
        reasons.append("EXECUTE capability declared")
    if ConnectorCapability.WRITE in caps:
        score += 2
        reasons.append("WRITE capability declared")

    # Riesgos declarados
    if contract.risks:
        score += len(contract.risks)
        reasons.append(f"{len(contract.risks)} risk(s) declared by author")

    # Sin fallback
    if not contract.fallback_connector:
        score += 1
        reasons.append("No fallback connector defined")

    # Logging deshabilitado
    if not contract.logs_enabled:
        score += 2
        reasons.append("Logging disabled — reduced observability")

    # Autenticación sin credenciales (NONE en conector con WRITE/EXECUTE)
    from connectors.core.types import AuthMethod
    if contract.auth_method == AuthMethod.NONE and caps & _HIGH_RISK_CAPABILITIES:
        score += 2
        reasons.append("No authentication on high-risk connector")

    # Clasificación
    if score >= 7:
        level = ContractRiskLevel.CRITICAL
    elif score >= 4:
        level = ContractRiskLevel.HIGH
    elif score >= 2:
        level = ContractRiskLevel.MEDIUM
    else:
        level = ContractRiskLevel.LOW

    return level, reasons


# ---------------------------------------------------------------------------
# Contracts Reader
# ---------------------------------------------------------------------------

class ContractsReader:
    """
    Lector y analizador de contratos de conectores.

    Responsabilidades:
    - Analizar contratos individuales (validación + riesgo)
    - Descubrir contratos desde el registry de conectores
    - Generar resumen global del estado de contratos
    - Filtrar contratos por familia, riesgo, o validez
    """

    def __init__(self) -> None:
        self._cache: Dict[str, ContractAnalysis] = {}
        logger.info("ContractsReader initialized")

    # -- Análisis individual ------------------------------------------------

    def analyze(self, contract: ConnectorContract) -> ContractAnalysis:
        """Analiza un contrato: valida y evalúa riesgo."""
        valid, violations = validate_contract(contract)
        risk_level, risk_reasons = _assess_risk(contract)

        analysis = ContractAnalysis(
            name=contract.name,
            family=contract.family.value,
            version=contract.version,
            valid=valid,
            violations=violations,
            risk_level=risk_level,
            risk_reasons=risk_reasons,
            capabilities=[c.value for c in contract.capabilities],
            has_fallback=contract.fallback_connector is not None,
            logs_enabled=contract.logs_enabled,
        )

        self._cache[contract.name] = analysis
        logger.info(
            "Contract analyzed: %s | valid=%s | risk=%s",
            contract.name, valid, risk_level.value,
        )
        return analysis

    # -- Descubrimiento desde registry --------------------------------------

    def discover_from_registry(self) -> ContractsSummary:
        """
        Descubre contratos desde los conectores registrados en el registry.
        Analiza cada uno que tenga un ConnectorContract asociado.
        """
        summary = ContractsSummary()
        discovered: List[ConnectorContract] = []

        try:
            from connectors.registry import get_connector_registry
            registry = get_connector_registry()
            for name, adapter in registry.list_all().items():
                contract = getattr(adapter, "contract", None)
                if isinstance(contract, ConnectorContract):
                    discovered.append(contract)
                    logger.debug("Discovered contract: %s", name)
        except Exception as exc:
            logger.warning("Could not discover from registry: %s", exc)

        return self.analyze_batch(discovered)

    # -- Análisis por lote --------------------------------------------------

    def analyze_batch(
        self,
        contracts: List[ConnectorContract],
    ) -> ContractsSummary:
        """Analiza una lista de contratos y genera resumen."""
        summary = ContractsSummary()
        summary.total = len(contracts)

        for contract in contracts:
            analysis = self.analyze(contract)
            summary.analyses.append(analysis)

            if analysis.valid:
                summary.valid += 1
            else:
                summary.invalid += 1

            # Agrupar por familia
            fam = analysis.family
            summary.by_family[fam] = summary.by_family.get(fam, 0) + 1

            # Agrupar por riesgo
            risk = analysis.risk_level.value
            summary.by_risk[risk] = summary.by_risk.get(risk, 0) + 1

        logger.info(
            "Batch analysis complete: %d total, %d valid, %d invalid",
            summary.total, summary.valid, summary.invalid,
        )
        return summary

    # -- Consultas ----------------------------------------------------------

    def get_analysis(self, name: str) -> Optional[ContractAnalysis]:
        """Retorna el análisis cacheado de un contrato."""
        return self._cache.get(name)

    def get_high_risk(self) -> List[ContractAnalysis]:
        """Retorna contratos con riesgo HIGH o CRITICAL."""
        return [
            a for a in self._cache.values()
            if a.risk_level in (ContractRiskLevel.HIGH, ContractRiskLevel.CRITICAL)
        ]

    def get_invalid(self) -> List[ContractAnalysis]:
        """Retorna contratos que no pasaron validación."""
        return [a for a in self._cache.values() if not a.valid]

    def get_by_family(self, family: str) -> List[ContractAnalysis]:
        """Filtra análisis por familia de conector."""
        return [a for a in self._cache.values() if a.family == family]


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_reader: Optional[ContractsReader] = None


def get_contracts_reader() -> ContractsReader:
    """Obtener la instancia singleton del lector de contratos."""
    global _reader
    if _reader is None:
        _reader = ContractsReader()
    return _reader
