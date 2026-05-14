import logging

from qiskit import qasm3, transpile
from qiskit_aer import AerSimulator

logger = logging.getLogger("classiq.runner")

DEFAULT_SHOTS = 1024


class QASMExecutionError(Exception):
    """Raised on QASM parse or simulation failure (terminal, no retry)."""


def execute_qasm3(qc: str, shots: int = DEFAULT_SHOTS) -> dict[str, int]:
    try:
        circuit = qasm3.loads(qc)
    except Exception as exc:
        raise QASMExecutionError(f"QASM3 parse failed: {exc}") from exc

    simulator = AerSimulator()
    try:
        transpiled = transpile(circuit, simulator)
        job = simulator.run(transpiled, shots=shots)
        counts = job.result().get_counts()
    except Exception as exc:
        raise QASMExecutionError(f"Simulation failed: {exc}") from exc

    return {str(k): int(v) for k, v in counts.items()}
