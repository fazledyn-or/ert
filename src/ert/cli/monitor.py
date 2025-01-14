# -*- coding: utf-8 -*-
import sys
from datetime import datetime, timedelta
from typing import Dict, Iterator, Optional, TextIO, Tuple, Union

from colors import color as ansi_color
from tqdm import tqdm

from ert.ensemble_evaluator import (
    EndEvent,
    FullSnapshotEvent,
    Snapshot,
    SnapshotUpdateEvent,
)
from ert.ensemble_evaluator.state import (
    ALL_REALIZATION_STATES,
    COLOR_FAILED,
    COLOR_FINISHED,
    JOB_STATE_FAILURE,
    REAL_STATE_TO_COLOR,
)
from ert.shared.status.utils import format_running_time

Color = Tuple[int, int, int]


def _no_color(
    s: str,
    fg: Optional[Color] = None,
    bg: Optional[Color] = None,
    style: Optional[Color] = None,
) -> str:
    """Alternate color method when no coloring is wanted. Conforms to the
    signature of ansi_color.color, wherein the first positional argument
    is the string to be (un-)colored."""
    return s


class Monitor:
    """Class for tracking and outputting the progress of a simulation @model,
    where progress is defined as a combination of fields on @tracker.

    Progress is printed to @out. @color_always decides whether or not coloring
    always should take place, i.e. even if @out does not support it.
    """

    dot = "■ "
    empty_bar_char = " "
    filled_bar_char = "█"
    bar_length = 30

    def __init__(self, out: TextIO = sys.stdout, color_always: bool = False) -> None:
        self._out = out
        self._snapshots: Dict[int, Snapshot] = {}
        self._start_time: Optional[datetime] = None
        self._colorize = ansi_color
        # If out is not (like) a tty, disable colors.
        if not out.isatty() and not color_always:
            self._colorize = _no_color

            # The dot adds no value without color, so remove it.
            self.dot = ""

    def monitor(
        self,
        events: Iterator[Union[FullSnapshotEvent, SnapshotUpdateEvent, EndEvent]],
    ) -> None:
        self._start_time = datetime.now()
        for event in events:
            if isinstance(event, FullSnapshotEvent):
                if event.snapshot is not None:
                    self._snapshots[event.iteration] = event.snapshot
                self._progress = event.progress
            elif isinstance(event, SnapshotUpdateEvent):
                if event.partial_snapshot is not None:
                    self._snapshots[event.iteration].merge_event(event.partial_snapshot)
                self._print_progress(event)
            if isinstance(event, EndEvent):
                self._print_result(event.failed, event.failed_msg)
                self._print_job_errors()
                return

    def _print_job_errors(self) -> None:
        failed_jobs: Dict[Optional[str], int] = {}
        for snapshot in self._snapshots.values():
            for real in snapshot.reals.values():
                for step in real.steps.values():
                    for job in step.jobs.values():
                        if job.status == JOB_STATE_FAILURE:
                            result = failed_jobs.get(job.error, 0)
                            failed_jobs[job.error] = result + 1
        for error, number_of_jobs in failed_jobs.items():
            print(f"{number_of_jobs} jobs failed due to the error: {error}")

    def _get_legends(self) -> str:
        statuses = ""
        latest_snapshot = self._snapshots[max(self._snapshots.keys())]
        total_count = len(latest_snapshot.reals)
        aggregate = latest_snapshot.aggregate_real_states()
        for state_ in ALL_REALIZATION_STATES:
            count = aggregate.get(state_, 0)
            _countstring = f"{count}/{total_count}"
            out = (
                f"{self._colorize(self.dot, fg=REAL_STATE_TO_COLOR[state_])}"
                f"{state_:10} {_countstring:>10}"
            )
            statuses += f"    {out}\n"
        return statuses

    def _print_result(self, failed: bool, failed_message: Optional[str]) -> None:
        if failed:
            msg = f"Experiment failed with the following error: {failed_message}"
            print(self._colorize(msg, fg=COLOR_FAILED), file=self._out)
        else:
            print(
                self._colorize("Experiment completed.", fg=COLOR_FINISHED),
                file=self._out,
            )

    def _print_progress(self, event: SnapshotUpdateEvent) -> None:
        if event.indeterminate:
            # indeterminate, no progress to be shown
            return

        current_phase = min(event.total_phases, event.current_phase + 1)
        if self._start_time is not None:
            elapsed = datetime.now() - self._start_time
        else:
            elapsed = timedelta()

        nphase = f" {current_phase}/{event.total_phases}"

        bar_format = "   {desc} |{bar}| {percentage:3.0f}% {unit}"
        tqdm.write(f"    --> {event.phase_name}", file=self._out)
        tqdm.write("\n", end="", file=self._out)
        with tqdm(total=100, ncols=100, bar_format=bar_format, file=self._out) as pbar:
            pbar.set_description_str(nphase, refresh=False)
            pbar.unit = f"{format_running_time(elapsed.seconds)}"
            pbar.update(event.progress * 100)
        tqdm.write("\n", end="", file=self._out)
        tqdm.write(self._get_legends(), file=self._out)
