#!/usr/bin/env python3
"""Create or update the site Computer and Codes from config/site.yaml.

Runs inside the container, against the live profile:

    ./run.py attach python3 tools/setup_site.py [config/site.yaml]

Idempotent: every record is looked up by label and updated in place, so a
second run keeps the same Computer and Code identities (PKs).  Only the
local AiiDA database is touched -- nothing here contacts the cluster.
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml
from aiida.manage.configuration import load_profile
from aiida.orm import Computer, InstalledCode, QueryBuilder
from aiida.tools import delete_nodes


def die(message):
    print(message, file=sys.stderr)
    raise SystemExit(1)


def find_by_label(label, klass):
    """Return the stored entity with LABEL, or None."""
    row = QueryBuilder().append(klass, filters={"label": label}).first()
    return row[0] if row else None


def computer_from_yaml(spec):
    """Create or update the Computer described by SPEC."""
    computer = find_by_label(spec["label"], Computer)
    created = computer is None
    if created:
        computer = Computer(label=spec["label"])
    computer.hostname = spec["hostname"]
    computer.transport_type = spec["transport"]
    computer.scheduler_type = spec["scheduler"]
    computer.set_workdir(spec["work_dir"])
    computer.set_mpirun_command(list(spec["mpirun_command"]))
    computer.set_property("mail_user", spec.get("mail_user", ""))
    computer.set_property("default_queue", spec.get("default_queue", ""))
    if not computer.is_stored:
        computer.store()
    # Auth params are transport configuration, not Computer attributes:
    # username, key_filename, safe_interval, proxy_command, port, ...
    # configure() validates the keys against the transport and stores the
    # AuthInfo, so an unknown key fails loudly instead of being ignored.
    computer.configure(**dict(spec.get("ssh", {})))
    return computer, created


def code_from_yaml(computer, spec):
    """Create or keep one InstalledCode on ``computer``.

    Stored codes are immutable in aiida-core (label/description aside), so
    there is no update in place.  When the definition matches, nothing is
    written -- that is what makes a second run identity-preserving.  A
    *changed* definition replaces the node; refused when provenance already
    references it, because deleting a code takes its calculations with it
    (the reason `verdi code delete` warns).  The MPI preference stays unset
    either way: tools/submit.py is the single source for it.
    """
    code = find_by_label(spec["label"], InstalledCode)
    wanted = {
        "computer": computer.pk,
        "filepath_executable": spec["executable"],
        "prepend_text": spec["prepend_text"],
        "default_calc_job_plugin": spec.get("plugin"),
    }
    if code is not None:
        have = {
            "computer": code.computer.pk,
            # filepath_executable comes back as a PurePosixPath.
            "filepath_executable": str(code.filepath_executable),
            "prepend_text": code.prepend_text,
            "default_calc_job_plugin": code.default_calc_job_plugin,
        }
        if have == wanted:
            return code, False
        if code.base.links.get_incoming().all():
            die(f"code {spec['label']}@{computer.label} changed but has "
                "provenance links; pick a new label or clean up by hand")
        delete_nodes([code.pk], dry_run=False)
        print(f"code {spec['label']}: definition changed, replaced node")
    # Verbatim from the yaml; the shipped example already carries the
    # SLURM_CONF save/restore guard and the GAUSS_SCRDIR block.
    code = InstalledCode(
        label=spec["label"],
        computer=computer,
        filepath_executable=spec["executable"],
        default_calc_job_plugin=wanted["default_calc_job_plugin"],
        prepend_text=wanted["prepend_text"],
    ).store()
    return code, True


def main(argv):
    path = Path(argv[1]) if len(argv) > 1 else Path("config/site.yaml")
    load_profile()
    config = yaml.safe_load(path.read_text())

    computer, created = computer_from_yaml(config["computer"])
    verb = "created" if created else "updated"
    print(f"computer {computer.label}: {verb} (pk {computer.pk})")

    for spec in config.get("codes", []):
        code, created = code_from_yaml(computer, spec)
        verb = "created" if created else "unchanged"
        print(f"code {code.label}@{computer.label}: {verb} (pk {code.pk})")


if __name__ == "__main__":
    main(sys.argv)
