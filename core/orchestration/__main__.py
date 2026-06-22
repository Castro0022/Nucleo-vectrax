"""
Comando único de orquestación de motores.

    python -m core.orchestration            # activa todo (perfil safe)
    python -m core.orchestration safe       # idem
    python -m core.orchestration full       # perfil full (interno; nunca LIVE)
    python -m core.orchestration status     # estado read-only por motor
    python -m core.orchestration dry        # preview (qué se activaría), read-only
"""
from __future__ import annotations

import json
import sys
from typing import List, Optional

from core.orchestration import activate_all, get_engine_status


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    cmd = (argv[0].lower() if argv else "safe")

    if cmd == "status":
        report = get_engine_status()
    elif cmd == "dry":
        report = activate_all("safe", dry_run=True)
    elif cmd in ("safe", "full"):
        report = activate_all(cmd)
    else:
        print("usage: python -m core.orchestration [safe|full|status|dry]")
        return 2

    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
