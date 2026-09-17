"""Abstract base class for execution engines."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional, Dict, Any


@dataclass
class ExecutionResult:
    """Result of a workload execution."""
    stdout: str = ""
    stderr: str = ""
    exit_code: int = -1
    error: Optional[str] = None

    @property
    def success(self) -> bool:
        return self.exit_code == 0 and self.error is None


class ExecutionEngine(ABC):
    """
    Abstract base class for workload execution engines.

    Subclasses implement the actual execution mechanism
    (e.g., Docker, subprocess, etc.).
    """

    @abstractmethod
    def is_available(self) -> bool:
        """Check if this execution engine is ready to run workloads."""
        ...

    @abstractmethod
    def execute(self, job_id: str, payload: Dict[str, Any]) -> ExecutionResult:
        """
        Execute a workload and return the result.

        Args:
            job_id: Unique identifier for this job.
            payload: Job payload containing workload specification.

        Returns:
            ExecutionResult with stdout, stderr, exit_code, and optional error.
        """
        ...
