"""Capa de servicio: FastAPI sobre el mismo `orchestrator.run_job` que usa
la CLI. El gate y el store son singletons de la app (un único ledger global, regla
1 de docs/PLAN.md); cada job corre en background y reporta su estado vía la API."""
