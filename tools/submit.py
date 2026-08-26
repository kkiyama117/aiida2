#!/usr/bin/env python3
"""Submit one Gaussian calculation from a calculation YAML.

Runs inside the container, against the live profile:

    ./run.py attach python3 tools/submit.py examples/h2o_opt.yaml [--dry-run]

The Slurm allocation and the Gaussian link0 block are both derived from the
calculation's ``resources``/``memory_gb``, so the two cannot drift apart:
``%nprocshared`` equals the allocated cores and ``%mem`` is the allocated
memory minus a fixed 1 GiB headroom (the executable, static memory, thread
stacks and I/O buffers sit on top of ``%mem``, which covers dynamic memory
only).  The 1 GiB constant stays provisional: KUDPC accounting reports
MaxRSS=0 even after a successful run (job 8449824), so there is no
measured peak to calibrate against.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import yaml
from aiida.engine import run_get_node, submit
from aiida.manage.configuration import load_profile
from aiida.orm import Dict, StructureData, load_code
from aiida.plugins import CalculationFactory
from aiida_slurm_rsc.mail import mail_scheduler_commands
from ase.io import read


def structure_from_file(path):
    """Read an ASE-readable file into a non-periodic StructureData.

    aiida-gaussian converts the structure with ``get_pymatgen_molecule()``,
    which refuses a periodic structure, so pbc is off and the cell is only a
    bounding box around the atoms.
    """
    atoms = read(path)
    span = atoms.get_positions().max(axis=0) - atoms.get_positions().min(axis=0)
    atoms.set_cell(span + 2.0, scale_atoms=False)  # 1 Å of padding each side
    structure = StructureData(ase=atoms)
    structure.set_pbc((False, False, False))
    return structure


def mail_commands(calc, computer):
    """Render the mail policy into custom_scheduler_commands."""
    policy = calc.get("mail", "default")
    if policy in ("none", ""):
        return ""
    if policy == "default":
        return mail_scheduler_commands(computer)
    if policy == "terminal":
        return mail_scheduler_commands(computer, terminal=True)
    if isinstance(policy, list):
        return mail_scheduler_commands(computer, mail_types=policy)
    raise ValueError(
        f"unknown mail policy {policy!r}; use default, terminal, none or a list of Slurm mail types"
    )


def build_builder(calc, code, computer):
    """Build the gaussian CalcJob builder for one calculation YAML."""
    resources = calc["resources"]
    if resources["num_mpiprocs_per_machine"] != 1:
        raise ValueError(
            "Gaussian runs as one shared-memory process, not as MPI: "
            f"num_mpiprocs_per_machine must be 1, got {resources['num_mpiprocs_per_machine']}"
        )
    memory_gb = calc["memory_gb"]
    if memory_gb < 2:
        raise ValueError(
            f"memory_gb must be at least 2 (1 GiB headroom is reserved under %mem), got {memory_gb}"
        )

    # One source for the pair: the link0 block mirrors the Slurm allocation.
    link0 = {
        "%chk": "aiida.chk",  # aiida-gaussian does not inject a checkpoint name
        "%nprocshared": resources["num_cores_per_mpiproc"],
        # Always an explicit unit: a bare %mem number means 8-byte words.
        "%mem": f"{memory_gb - 1}GB",
    }
    parameters = {
        "functional": calc["functional"],
        "basis_set": calc["basis_set"],
        "route_parameters": calc.get("route_parameters", {}),
        "charge": calc.get("charge", 0),
        "multiplicity": calc.get("multiplicity", 1),
        "link0_parameters": link0,
    }

    builder = CalculationFactory("gaussian").get_builder()
    builder.code = code
    builder.structure = structure_from_file(calc["structure"])
    builder.parameters = Dict(parameters)
    builder.metadata.options.resources = resources
    builder.metadata.options.max_memory_kb = memory_gb * 1024 * 1024
    builder.metadata.options.max_wallclock_seconds = calc["max_wallclock_seconds"]
    # The only place withmpi is decided: mpirun_command: [srun] only
    # prefixes g16 when this is True.
    builder.metadata.options.withmpi = True

    queue = calc.get("queue") or computer.get_property("default_queue", "")
    if queue:
        builder.metadata.options.queue_name = queue
    builder.metadata.options.custom_scheduler_commands = mail_commands(calc, computer)
    return builder


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("calc", help="calculation YAML, e.g. examples/h2o_opt.yaml")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="render the submit script and input file without submitting",
    )
    args = parser.parse_args(argv)

    load_profile()
    calc = yaml.safe_load(Path(args.calc).read_text())
    code = load_code(calc["code"])
    builder = build_builder(calc, code, code.computer)

    if args.dry_run:
        # Without this flag the engine really uploads over SSH; with it,
        # presubmit renders _aiidasubmit.sh/aiida.inp locally instead.
        builder.metadata.dry_run = True
        _, node = run_get_node(builder)
        folder = node.dry_run_info["folder"]
        print(f"dry-run folder: {folder}")
        print(
            f"submit script:  {os.path.join(folder, node.dry_run_info['script_filename'])}"
        )
        print(f"input file:     {os.path.join(folder, 'aiida.inp')}")
    else:
        node = submit(builder)
        print(f"submitted calculation node pk={node.pk}")


if __name__ == "__main__":
    main(sys.argv[1:])
