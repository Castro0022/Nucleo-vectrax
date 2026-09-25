"""core/trading — dominio de trading gobernado por el Núcleo.

Capas (ver core/trading/contracts.py para los objetos que viajan entre
ellas):

    TradeDecisionEngine  -> "quiero ENTER/HOLD/REDUCE/EXIT" (criterio)
    RiskGate              -> "¿está permitido, o qué lo sustituye?" (ciego)
    DecisionAuthority      -> "¿está autorizado?" (core/operator, sin cambios)
    PositionManager        -> actualiza estado de la posición
    AutoExecutor           -> ejecuta la orden en el broker

Ninguna capa pasa `dict` sueltos entre sí: solo los objetos tipados de
`contracts.py`.
"""
