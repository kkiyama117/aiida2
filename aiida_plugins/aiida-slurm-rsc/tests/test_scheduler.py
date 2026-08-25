"""Submit-script header generation. No cluster, no profile, no daemon."""

import pytest
from aiida.schedulers.datastructures import JobTemplate

from aiida_slurm_rsc.scheduler import SlurmRscScheduler

FORBIDDEN = ["--nodes", "--ntasks", "--cpus-per-task", "--mem=", "--qos", "--account", "--get-user-env"]


def make_template(**overrides):
    sched = SlurmRscScheduler()
    res_kwargs = {
        "num_machines": 1,
        "num_mpiprocs_per_machine": 4,
        "num_cores_per_mpiproc": 1,
    }
    for k in list(res_kwargs):
        if k in overrides:
            res_kwargs[k] = overrides.pop(k)
    job_resource = sched.create_job_resource(**res_kwargs)
    tmpl = JobTemplate()
    tmpl.job_resource = job_resource
    tmpl.max_memory_kb = 8 * 1024 * 1024  # 8 GiB
    tmpl.job_name = "qe_test"
    tmpl.queue_name = "gr10641a"
    tmpl.max_wallclock_seconds = 3600
    tmpl.rerunnable = True
    tmpl.sched_output_path = "_aiidasubmit.sh.out"
    tmpl.sched_error_path = "_aiidasubmit.sh.err"
    for k, v in overrides.items():
        setattr(tmpl, k, v)
    return sched, tmpl


def header(**overrides):
    sched, tmpl = make_template(**overrides)
    return sched._get_submit_script_header(tmpl)


def test_basic_header():
    text = header()
    assert "#SBATCH --rsc p=1:t=4:c=4:m=8G" in text, text
    assert "#SBATCH --partition=gr10641a" in text
    assert "#SBATCH --time=01:00:00" in text
    for bad in FORBIDDEN:
        assert bad not in text, f"forbidden option emitted: {bad}\n{text}"


def test_cores_per_mpiproc():
    assert "#SBATCH --rsc p=1:t=8:c=8:m=8G" in header(
        num_mpiprocs_per_machine=2, num_cores_per_mpiproc=4
    )


def test_memory_rounds_up_to_gib():
    assert "#SBATCH --rsc p=1:t=4:c=4:m=10G" in header(max_memory_kb=9 * 1024 * 1024 + 1)


def test_memory_omitted_when_unset():
    text = header(max_memory_kb=None)
    assert "#SBATCH --rsc p=1:t=4:c=4" in text
    assert ":m=" not in text


def test_no_requeue():
    assert "#SBATCH --no-requeue" in header(rerunnable=False)


def test_priority_becomes_nice():
    assert "#SBATCH --nice=1000" in header(priority="1000")


def test_custom_scheduler_commands_are_appended():
    """The channel mail_scheduler_commands() rides on."""
    text = header(custom_scheduler_commands="#SBATCH --mail-user=someone@example.org")
    assert text.endswith("#SBATCH --mail-user=someone@example.org")


def test_no_mail_without_job_template_email():
    """aiida-core leaves JobTemplate.email unset, so the header carries no mail."""
    assert "--mail-" not in header()


def test_job_template_email_is_still_honoured():
    """Kept for the day aiida-core starts filling the template in."""
    text = header(email="someone@example.org", email_on_terminated=True)
    assert "#SBATCH --mail-user=someone@example.org" in text
    assert "#SBATCH --mail-type=END,FAIL" in text


def test_job_template_email_without_events_sends_nothing():
    """An address with no event would leave Slurm to its own default."""
    assert "--mail-" not in header(email="someone@example.org")


def test_bad_memory_raises():
    with pytest.raises(ValueError, match="max_memory_kb"):
        header(max_memory_kb="lots")


def test_bad_wallclock_raises():
    with pytest.raises(ValueError, match="max_wallclock_seconds"):
        header(max_wallclock_seconds=0)


def test_resources_are_required():
    sched, tmpl = make_template()
    tmpl.job_resource = None
    with pytest.raises(ValueError, match="Job resources"):
        sched._get_submit_script_header(tmpl)
