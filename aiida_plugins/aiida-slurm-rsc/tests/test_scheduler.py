"""Unit test for SlurmRscScheduler header generation (no cluster interaction)."""
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
    tmpl.email = "kiyama.kouhei.54v@st.kyoto-u.ac.jp"
    tmpl.email_on_terminated = True
    tmpl.rerunnable = True
    tmpl.sched_output_path = "_aiidasubmit.sh.out"
    tmpl.sched_error_path = "_aiidasubmit.sh.err"
    for k, v in overrides.items():
        setattr(tmpl, k, v)
    return sched, tmpl


def test_basic_header():
    sched, tmpl = make_template()
    header = sched._get_submit_script_header(tmpl)
    print("=== header ===")
    print(header)
    assert "#SBATCH --rsc p=1:t=4:c=4:m=8G" in header, header
    assert "#SBATCH --partition=gr10641a" in header
    assert "#SBATCH --time=01:00:00" in header
    assert "#SBATCH --mail-user=kiyama.kouhei.54v@st.kyoto-u.ac.jp" in header
    assert "#SBATCH --mail-type=FAIL" in header and "#SBATCH --mail-type=END" in header
    for bad in FORBIDDEN:
        assert bad not in header, f"forbidden option emitted: {bad}\n{header}"
    print("PASS: basic header")


def test_cores_per_mpiproc():
    sched, tmpl = make_template(num_mpiprocs_per_machine=2, num_cores_per_mpiproc=4)
    header = sched._get_submit_script_header(tmpl)
    assert "#SBATCH --rsc p=1:t=8:c=8:m=8G" in header, header
    print("PASS: cores = mpiprocs x cores_per_mpiproc")


def test_memory_rounding():
    sched, tmpl = make_template(max_memory_kb=9 * 1024 * 1024 + 1)  # just over 9 GiB
    header = sched._get_submit_script_header(tmpl)
    assert "#SBATCH --rsc p=1:t=4:c=4:m=10G" in header, header
    print("PASS: memory rounds up to GiB")


def test_no_memory():
    sched, tmpl = make_template(max_memory_kb=None)
    header = sched._get_submit_script_header(tmpl)
    assert "#SBATCH --rsc p=1:t=4:c=4" in header, header
    assert ":m=" not in header
    print("PASS: memory omitted when unset")


def test_no_requeue():
    sched, tmpl = make_template(rerunnable=False)
    header = sched._get_submit_script_header(tmpl)
    assert "#SBATCH --no-requeue" in header
    print("PASS: --no-requeue")


if __name__ == "__main__":
    test_basic_header()
    test_cores_per_mpiproc()
    test_memory_rounding()
    test_no_memory()
    test_no_requeue()
    print("\nALL TESTS PASSED")
