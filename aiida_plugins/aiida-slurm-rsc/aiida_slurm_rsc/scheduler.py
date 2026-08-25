"""AiiDA scheduler plugin for KUDPC Camphor (sp).

KUDPC forbids the standard Slurm resource options (``--nodes``,
``--ntasks-per-node``, ``--cpus-per-task``, ``--mem``, ``--qos``) and requires
the custom resource spec::

    #SBATCH --rsc p=1:t=16:c=16:m=16G

This plugin subclasses AiiDA's ``SlurmScheduler`` and reimplements only the
submit-script header, emitting ``--rsc`` (same convention as the group's
``slurm-async-runner`` pipeline) and skipping every option that KUDPC forbids.
All job-queue parsing (squeue/sacct) is inherited unchanged.
"""

from __future__ import annotations

import math
import re
import string

from aiida.schedulers.plugins.slurm import SlurmScheduler
from aiida.schedulers.datastructures import JobTemplate

__all__ = ("SlurmRscScheduler",)


class SlurmRscScheduler(SlurmScheduler):
    """Slurm scheduler for KUDPC Camphor (sp) using the custom ``--rsc`` spec."""

    def _get_submit_script_header(self, job_tmpl: JobTemplate) -> str:
        """Return the submit script header, using the parameters from the job_tmpl.

        Only options verified to be accepted by KUDPC ``sbatch`` are emitted.
        Resource allocation is expressed via ``--rsc p=..:t=..:c=..:m=..G``
        (p = nodes, t = threads, c = cores, m = memory per node).
        """
        lines = []

        if job_tmpl.submit_as_hold:
            lines.append('#SBATCH -H')

        if job_tmpl.rerunnable:
            lines.append('#SBATCH --requeue')
        else:
            lines.append('#SBATCH --no-requeue')

        if job_tmpl.email:
            # If not specified, but email events are set, SLURM
            # sends the mail to the job owner by default
            lines.append(f'#SBATCH --mail-user={job_tmpl.email}')

        if job_tmpl.email_on_started:
            lines.append('#SBATCH --mail-type=BEGIN')
        if job_tmpl.email_on_terminated:
            lines.append('#SBATCH --mail-type=FAIL')
            lines.append('#SBATCH --mail-type=END')

        if job_tmpl.job_name:
            # Same sanitization as the stock Slurm plugin
            job_title = re.sub(r'[^a-zA-Z0-9_.-]+', '', job_tmpl.job_name)
            if not job_title or (job_title[0] not in string.ascii_letters + string.digits):
                job_title = f'j{job_title}'
            job_title = job_title[:128]
            lines.append(f'#SBATCH --job-name="{job_title}"')

        if job_tmpl.sched_output_path:
            lines.append(f'#SBATCH --output={job_tmpl.sched_output_path}')

        if job_tmpl.sched_join_files:
            if job_tmpl.sched_error_path:
                self.logger.info(
                    'sched_join_files is True, but sched_error_path is set in SLURM script; ignoring sched_error_path'
                )
        elif job_tmpl.sched_error_path:
            lines.append(f'#SBATCH --error={job_tmpl.sched_error_path}')
        else:
            # To avoid automatic join of files
            lines.append('#SBATCH --error=slurm-%j.err')

        if job_tmpl.queue_name:
            lines.append(f'#SBATCH --partition={job_tmpl.queue_name}')

        if job_tmpl.priority:
            lines.append(f'#SBATCH --nice={job_tmpl.priority}')

        if not job_tmpl.job_resource:
            raise ValueError('Job resources (as the num_machines) are required for the SLURM scheduler plugin')

        # --- KUDPC custom resource spec (replaces --nodes/--ntasks-per-node/--cpus-per-task/--mem) ---
        num_machines = job_tmpl.job_resource.num_machines
        num_mpiprocs = job_tmpl.job_resource.num_mpiprocs_per_machine or 1
        num_cores = job_tmpl.job_resource.num_cores_per_mpiproc or 1
        total_cores = num_mpiprocs * num_cores

        rsc = f'p={num_machines}:t={total_cores}:c={total_cores}'

        if job_tmpl.max_memory_kb is not None:
            try:
                physical_memory_kb = int(job_tmpl.max_memory_kb)
                if physical_memory_kb < 0:
                    raise ValueError
            except ValueError:
                raise ValueError(
                    f'max_memory_kb must be a non-negative integer (in kB)! It is instead `{job_tmpl.max_memory_kb}`'
                )
            if physical_memory_kb > 0:
                # Round up to the next GiB (KUDPC rsc memory is expressed in G)
                rsc += f':m={math.ceil(physical_memory_kb / 1024 / 1024)}G'

        lines.append(f'#SBATCH --rsc {rsc}')

        if job_tmpl.max_wallclock_seconds is not None:
            try:
                tot_secs = int(job_tmpl.max_wallclock_seconds)
                if tot_secs <= 0:
                    raise ValueError
            except ValueError:
                raise ValueError(
                    'max_wallclock_seconds must be a positive integer (in seconds)! '
                    f"It is instead '{job_tmpl.max_wallclock_seconds}'"
                )
            days = tot_secs // 86400
            tot_hours = tot_secs % 86400
            hours = tot_hours // 3600
            tot_minutes = tot_hours % 3600
            minutes = tot_minutes // 60
            seconds = tot_minutes % 60
            if days == 0:
                lines.append(f'#SBATCH --time={hours:02d}:{minutes:02d}:{seconds:02d}')
            else:
                lines.append(f'#SBATCH --time={days:d}-{hours:02d}:{minutes:02d}:{seconds:02d}')

        if job_tmpl.custom_scheduler_commands:
            lines.append(job_tmpl.custom_scheduler_commands)

        return '\n'.join(lines)
