from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from ert.job_queue import RunStatus
    from ert.storage import EnsembleAccessor


@dataclass
class RunArg:  # pylint: disable=too-many-instance-attributes
    run_id: str
    ensemble_storage: EnsembleAccessor
    iens: int
    itr: int
    runpath: str
    job_name: str
    active: bool = True
    # Below here is legacy related to Everest
    queue_index: Optional[int] = None
    submitted: bool = False
    run_status: Optional[RunStatus] = None
