"""In-container dry-run integration test (plan Step 6).

Run inside the container with the services up:

    ./run.py attach python3 -m pytest -q tests/test_dry_run_integration.py

Hermetic: it creates its own Computer (slurm_rsc + core.local) and an
InstalledCode whose prepend_text mimics the site one, so it needs neither
config/site.yaml nor any network.  It asserts over the generated
_aiidasubmit.sh and aiida.inp -- the first exercise of slurm_rsc against a
real calculation rather than a hand-built JobTemplate.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aiida.manage.configuration import load_profile  # noqa: E402

load_profile()

from aiida.engine import run_get_node  # noqa: E402
from aiida.orm import Computer, InstalledCode, QueryBuilder  # noqa: E402

from tools.submit import build_builder  # noqa: E402

REPO = Path(__file__).resolve().parents[1]

# Mirrors examples/h2o_opt.yaml: 4 GB -> %mem=3GB, 4 cores -> %nprocshared=4.
CALC = {
    "code": "dry-run-g16@dry-run-test",
    "structure": str(REPO / "examples" / "h2o.xyz"),
    "charge": 0,
    "multiplicity": 1,
    "functional": "B3LYP",
    "basis_set": "6-31G(d)",
    "route_parameters": {"opt": None},
    "resources": {
        "num_machines": 1,
        "num_mpiprocs_per_machine": 1,
        "num_cores_per_mpiproc": 4,
    },
    "memory_gb": 4,
    "max_wallclock_seconds": 1800,
    "queue": "gr41caa",
    "mail": "default",
}

# Same shape as the site example: keep SLURM_CONF across the module restore,
# then give Gaussian a per-job scratch directory.
PREPEND_TEXT = """\
SLURM_CONF_SAVED="${SLURM_CONF:-}"
. /usr/share/Modules/init/bash
module restore gaussian_A -f
export SLURM_CONF="${SLURM_CONF_SAVED}"
export GAUSS_SCRDIR="${SLURM_TMPDIR:-/tmp}/g16_${SLURM_JOB_ID:-$$}"
mkdir -p "${GAUSS_SCRDIR}"
"""

FORBIDDEN = ("--nodes", "--ntasks", "--cpus-per-task", "--mem", "--qos")


def get_computer():
    row = QueryBuilder().append(Computer, filters={"label": "dry-run-test"}).first()
    if row is not None:
        return row[0]
    computer = Computer(
        label="dry-run-test",
        hostname="dry-run-test",
        transport_type="core.local",
        scheduler_type="slurm_rsc",
        workdir="/tmp/dry-run-test",
        metadata={"mpirun_command": ["srun"]},
    )
    computer.store()
    computer.configure()
    return computer


def get_code(computer):
    row = QueryBuilder().append(InstalledCode, filters={"label": "dry-run-g16"}).first()
    if row is not None:
        return row[0]
    code = InstalledCode(
        label="dry-run-g16",
        computer=computer,
        filepath_executable="g16",
        prepend_text=PREPEND_TEXT,
    )
    code.store()
    return code


def dry_run():
    """Run the dry run once and return (submit_script, input_file) text."""
    computer = get_computer()
    code = get_code(computer)
    builder = build_builder(CALC, code, computer)
    builder.metadata.dry_run = True
    builder.metadata.label = "dry-run-test"
    _, node = run_get_node(builder)
    folder = Path(node.dry_run_info["folder"])
    return (folder / "_aiidasubmit.sh").read_text(), (folder / "aiida.inp").read_text()


def test_submit_script_has_exact_kudpc_resources():
    script, _ = dry_run()
    assert "#SBATCH --rsc p=1:t=4:c=4:m=4G" in script, script


def test_submit_script_omits_forbidden_options():
    script, _ = dry_run()
    for bad in FORBIDDEN:
        assert bad not in script, f"forbidden option emitted: {bad}\n{script}"


def test_submit_script_runs_srun_g16_over_stdin():
    script, _ = dry_run()
    assert "'srun' 'g16' < 'aiida.inp' > 'aiida.out'" in script, script


def test_input_matches_allocated_cores_and_memory():
    _, inp = dry_run()
    assert "%nprocshared=4" in inp, inp
    assert "%mem=3GB" in inp, inp  # memory_gb 4 minus the 1 GiB headroom
    assert "%chk=aiida.chk" in inp, inp
