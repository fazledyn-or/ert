from __future__ import annotations

from functools import partial
from threading import Thread
from time import sleep
from typing import TYPE_CHECKING, Any, List, Optional, Tuple

import numpy as np

from ert.config import HookRuntime
from ert.job_queue import Driver, JobQueue, JobQueueManager, RunStatus
from ert.realization_state import RealizationState
from ert.run_context import RunContext
from ert.runpaths import Runpaths

from .forward_model_status import ForwardModelStatus

if TYPE_CHECKING:
    import numpy.typing as npt

    from ert.enkf_main import EnKFMain
    from ert.job_queue import JobStatus
    from ert.run_arg import RunArg
    from ert.storage import EnsembleAccessor


def _run_forward_model(
    ert: "EnKFMain", job_queue: "JobQueue", run_context: "RunContext"
) -> int:
    # run simplestep
    for realization_nr in run_context.active_realizations:
        run_context.sim_fs.update_realization_state(
            realization_nr,
            [
                RealizationState.UNDEFINED,
                RealizationState.LOAD_FAILURE,
            ],
            RealizationState.INITIALIZED,
        )
    run_context.sim_fs.sync()

    # start queue
    max_runtime: Optional[int] = ert.analysisConfig().max_runtime
    if max_runtime == 0:
        max_runtime = None

    # submit jobs
    for index, run_arg in enumerate(run_context):
        if not run_context.is_active(index):
            continue
        job_queue.add_job_from_run_arg(
            run_arg,
            ert.resConfig().queue_config.job_script,
            max_runtime,
            ert.get_num_cpu(),
        )

    queue_evaluators = None
    if (
        ert.analysisConfig().stop_long_running
        and ert.analysisConfig().minimum_required_realizations > 0
    ):
        queue_evaluators = [
            partial(
                job_queue.stop_long_running_jobs,
                ert.analysisConfig().minimum_required_realizations,
            )
        ]

    jqm = JobQueueManager(job_queue, queue_evaluators)
    jqm.execute_queue()

    # deactivate failed realizations
    totalOk = 0
    for index, run_arg in enumerate(run_context):
        if run_context.is_active(index):
            if run_arg.run_status in (
                RunStatus.JOB_LOAD_FAILURE,
                RunStatus.JOB_RUN_FAILURE,
            ):
                run_context.deactivate_realization(index)
            else:
                totalOk += 1

    run_context.sim_fs.sync()

    return totalOk


class SimulationContext:
    def __init__(  # pylint: disable=too-many-arguments
        self,
        ert: "EnKFMain",
        sim_fs: EnsembleAccessor,
        mask: npt.NDArray[np.bool_],
        itr: int,
        case_data: List[Tuple[Any, Any]],
    ):
        self._ert = ert
        max_runtime = ert.analysisConfig().max_runtime
        self._mask = mask

        job_queue = JobQueue(
            Driver.create_driver(ert.get_queue_config()),
            max_submit=ert.get_queue_config().max_submit,
        )
        job_queue.set_max_job_duration(max_runtime or 0)
        self._queue_manager = JobQueueManager(job_queue)
        # fill in the missing geo_id data
        global_substitutions = ert.get_context()
        for sim_id, (geo_id, _) in enumerate(case_data):
            if mask[sim_id]:
                global_substitutions[f"<GEO_ID_{sim_id}_{itr}>"] = str(geo_id)
        self._run_context = RunContext(
            sim_fs=sim_fs,
            runpaths=Runpaths(
                jobname_format=ert.getModelConfig().jobname_format_string,
                runpath_format=ert.getModelConfig().runpath_format_string,
                filename=str(ert.resConfig().runpath_file),
                substitute=global_substitutions.substitute_real_iter,
            ),
            initial_mask=mask,
            iteration=itr,
        )

        for realization_nr in self._run_context.active_realizations:
            self._run_context.sim_fs.state_map[
                realization_nr
            ] = RealizationState.INITIALIZED
        self._ert.createRunPath(self._run_context)
        self._ert.runWorkflows(
            HookRuntime.PRE_SIMULATION, None, self._run_context.sim_fs
        )
        self._sim_thread = self._run_simulations_simple_step()

        # Wait until the queue is active before we finish the creation
        # to ensure sane job status while running
        while self.isRunning() and not self._queue_manager.isRunning():
            sleep(0.1)

    def get_run_args(self, iens: int) -> "RunArg":
        """
        raises an exception if no iens simulation found

        :param iens: realization number
        :return: run_args for the realization
        """
        for run_arg in iter(self._run_context):
            if run_arg is not None and run_arg.iens == iens:
                return run_arg
        raise KeyError(f"No such realization: {iens}")

    def _run_simulations_simple_step(self) -> Thread:
        sim_thread = Thread(
            target=lambda: _run_forward_model(
                self._ert, self._queue_manager.queue, self._run_context
            )
        )
        sim_thread.start()
        return sim_thread

    def __len__(self) -> int:
        return len(self._mask)

    def isRunning(self) -> bool:
        # TODO: Should separate between running jobs and having loaded all data
        return self._sim_thread.is_alive() or self._queue_manager.isRunning()

    def getNumPending(self) -> int:
        return self._queue_manager.getNumPending()

    def getNumRunning(self) -> int:
        return self._queue_manager.getNumRunning()

    def getNumSuccess(self) -> int:
        return self._queue_manager.getNumSuccess()

    def getNumFailed(self) -> int:
        return self._queue_manager.getNumFailed()

    def getNumWaiting(self) -> int:
        return self._queue_manager.getNumWaiting()

    def didRealizationSucceed(self, iens: int) -> bool:
        queue_index = self.get_run_args(iens).queue_index
        if queue_index is None:
            raise ValueError("Queue index not set")
        return self._queue_manager.didJobSucceed(queue_index)

    def didRealizationFail(self, iens: int) -> bool:
        # For the purposes of this class, a failure should be anything (killed
        # job, etc) that is not an explicit success.
        return not self.didRealizationSucceed(iens)

    def isRealizationFinished(self, iens: int) -> bool:
        run_arg = self.get_run_args(iens)

        queue_index = run_arg.queue_index
        if queue_index is not None:
            return self._queue_manager.isJobComplete(queue_index)
        else:
            # job was not submitted
            return False

    def __repr__(self) -> str:
        running = "running" if self.isRunning() else "not running"
        numRunn = self.getNumRunning()
        numSucc = self.getNumSuccess()
        numFail = self.getNumFailed()
        numWait = self.getNumWaiting()
        return (
            f"SimulationContext({running}, #running = {numRunn}, "
            f"#success = {numSucc}, #failed = {numFail}, #waiting = {numWait})"
        )

    def get_sim_fs(self) -> EnsembleAccessor:
        return self._run_context.sim_fs

    def stop(self) -> None:
        self._queue_manager.stop_queue()
        self._sim_thread.join()

    def job_progress(self, iens: int) -> Optional[ForwardModelStatus]:
        """Will return a detailed progress of the job.

        The progress report is obtained by reading a file from the filesystem,
        that file is typically created by another process running on another
        machine, and reading might fail due to NFS issues, simultanoues write
        and so on. If loading valid json fails the function will sleep 0.10
        seconds and retry - eventually giving up and returning None. Also for
        jobs which have not yet started the method will return None.

        When the method succeeds in reading the progress file from the file
        system the return value will be an object with properties like this:

            progress.start_time
            progress.end_time
            progress.run_id
            progress.jobs = [
                (job1.name, job1.start_time, job1.end_time, job1.status, job1.error_msg),
                (job2.name, job2.start_time, job2.end_time, job2.status, job2.error_msg),
                (jobN.name, jobN.start_time, jobN.end_time, jobN.status, jobN.error_msg)
            ]
        """  # noqa
        run_arg = self.get_run_args(iens)

        queue_index = run_arg.queue_index
        if queue_index is None:
            # job was not submitted
            return None
        if self._queue_manager.isJobWaiting(queue_index):
            return None

        return ForwardModelStatus.load(run_arg.runpath)

    def run_path(self, iens: int) -> str:
        """
        Will return the path to the simulation.
        """
        return self.get_run_args(iens).runpath

    def job_status(self, iens: int) -> Optional["JobStatus"]:
        """Will query the queue system for the status of the job."""
        run_arg = self.get_run_args(iens)
        queue_index = run_arg.queue_index
        if queue_index is None:
            # job was not submitted
            return None
        return self._queue_manager.getJobStatus(queue_index)
