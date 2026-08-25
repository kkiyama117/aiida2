# Phase 1 plan: one H2O Gaussian vertical slice

Status: **planned** — implements Phase 1 of
[`docs/gaussian-plan.md`](../gaussian-plan.md). Nothing below has been
implemented yet.

## Goal

Submit one H2O geometry optimisation on KUDPC Camphor (`sp`) through AiiDA,
parse it, and inspect its results. This proves container, daemon, SSH
transport, `slurm_rsc`, `aiida-gaussian`, the Gaussian environment and the
retrieval path together. No workflow logic is added.

## Current state (verified in the repository)

| item | state |
| --- | --- |
| `run.py` | `up`/`down`/`attach`/`shell`/`status` implemented; `BINDS` hardcoded to `.aiida`, `.postgresql`, `.rabbitmq`, `aiida_run` |
| `containerfiles/aiida.def` | bakes `aiida-slurm-rsc` with entry-point assertion; no `aiida-gaussian` |
| `tools/`, `config/`, `examples/` | do not exist — all created by this plan |
| `slurm_rsc` mail helper | already reads `Computer.set_property("mail_user")`; matches the `site.yaml` design |
| `.gitignore` | has `data/`, `*.sif`; needs `config/site.yaml`, `config/binds.conf` and `submit_test/` |

## Steps (in dependency order)

### Step 1 — bake `aiida-gaussian` into the image

Add to `%post` in `containerfiles/aiida.def`, next to `aiida-slurm-rsc`,
pinning `aiida-gaussian==2.2.0`. Use normal dependency resolution: unlike
the local `aiida-slurm-rsc` plugin, `aiida-gaussian` has a substantial
dependency tree and `--no-deps` would produce an incomplete installation.
Constrain `aiida-core==2.9.0` and `numpy==2.2.6` so resolution cannot replace
the versions supplied by the pinned base image. Transitive scientific
packages remain resolver-selected for Python 3.10, so their versions and the
resulting image size can still move between rebuilds.

Assert `gaussian` in the `aiida.calculations` entry-point group and
`gaussian.base` in `aiida.parsers`.

Rebuild: `./run.py down` → `apptainer build --fakeroot --force …` →
`./run.py up`. pymatgen and ase add a few hundred MB.

### Step 2 — teach `run.py` about `config/binds.conf` (~+25 lines)

If the file exists, parse it: one `src:dst[:ro]` per line, `#` comments,
expand `~`, and **fail loudly on a missing source** before Apptainer can
create an empty directory. Reject destinations that would shadow
`/home/aiida/.local` (where aiida-core is installed) or the managed
`/home/aiida/.aiida` state. Append the mounts returned by `binds()` for
`up`, `shell` and `attach` alike. No file means no extra mounts — the default
session still carries no credentials.

Add a host-side unit test for comments, `~` expansion, missing sources and
protected destinations. `run.py` and this test remain standard-library-only.

### Step 3 — configuration and example files (committed)

```text
config/site.example.yaml    placeholders + comments
config/binds.example.conf   ssh-key bind example
examples/h2o_opt.yaml       H2O B3LYP/6-31G(d) opt
examples/h2o.xyz
```

Add `config/site.yaml`, `config/binds.conf` and AiiDA's dry-run output
directory `submit_test/` to `.gitignore`.

The bind example mounts the private key and `known_hosts` as individual
read-only files. The host key must already be registered in `known_hosts`;
do not weaken AiiDA's default reject policy. `site.example.yaml` comments
show optional `core.ssh` parameters such as `proxy_command`, `port` and
`use_login_shell` without requiring them for the direct KUDPC setup.

### Step 4 — `tools/setup_site.py` (idempotent)

Reads `config/site.yaml` inside the container; standard library plus PyYAML.
Creates or updates:

- the `Computer`: label `sp`, scheduler `slurm_rsc`, transport `core.ssh`,
  `mpirun_command: [srun]`, with `mail_user` and `default_queue` stored as
  properties (aiida-core has no native default-queue field);
- its transport auth configuration from the nested `ssh` mapping, including
  `username`, `key_filename` and `safe_interval` (which is an auth parameter,
  not a `Computer` attribute);
- each `InstalledCode` (`g16@sp`) whose `prepend_text` restores the Gaussian
  module, preserves the pre-module value (including an unset value) of
  `SLURM_CONF`, and exports/mkdirs `GAUSS_SCRDIR`. This prevents the module
  restore from leaving a compute-node-only Slurm configuration that breaks
  `srun`. Leave the code's MPI preference unset; the calculation builder is
  the single source for it.

Run as `./run.py attach python3 tools/setup_site.py`. Idempotency check:
run twice and verify the same Computer and Code identities. Creating and
configuring these records is local and must not contact KUDPC.

`verdi computer test sp` is deferred to Step 7 because it requires the real
SSH key, account and network.

### Step 5 — `tools/submit.py`

Turns a calculation YAML into a `gaussian` builder: structure read by ASE
(`pbc=False` + bounding-box cell), Gaussian link0 parameters and the Slurm
allocation derived from one source (`resources` + `memory_gb`), and mail
policy via `mail_scheduler_commands()`.

The builder must:

- set `metadata.options.withmpi = True` in this one place, so
  `mpirun_command: [srun]` actually prefixes `g16`;
- require `num_mpiprocs_per_machine == 1`: Gaussian uses one shared-memory
  process, not multiple MPI processes;
- set `%nprocshared` to `num_cores_per_mpiproc`;
- request `memory_gb` from Slurm but set Gaussian `%mem` to
  `floor(0.75 * memory_gb)` GiB, rejecting values that leave less than 1 GiB.
  Gaussian `%mem` covers dynamic memory and must leave room for the executable,
  static memory and buffers;
- use the calculation's `queue` or fall back to the Computer's
  `default_queue` property;
- set `%chk=aiida.chk` explicitly. Neither aiida-gaussian nor pymatgen injects
  a checkpoint name; the checkpoint remains in the remote folder and is not
  retrieved in Phase 1.

For `--dry-run`, set `builder.metadata.dry_run = True`, call
`engine.run_get_node(builder)`, and print the folder from `node.dry_run_info`.
Use `run_get_node` so the synchronous dry-run path is explicit.

### Step 6 — dry-run verification (no cluster needed)

Add an in-container integration test that performs the dry run and asserts
over `_aiidasubmit.sh` and `aiida.inp`:

- present: `#SBATCH --rsc p=1:t=4:c=4:m=4G`;
- absent: `--nodes`, `--ntasks*`, `--cpus-per-task`, `--mem`, `--qos`;
- command line is `'srun' 'g16' < 'aiida.inp' > 'aiida.out'`;
- `%nprocshared=4`, `%mem=3GB`, and `%chk=aiida.chk`.

This is the first exercise of `slurm_rsc` against a real calculation rather
than a hand-built `JobTemplate`. It requires a stored Computer and Code but
uses `LocalTransport`, so it needs neither SSH nor cluster access. Manual
inspection remains useful for diagnosis but is not the acceptance gate.

### Step 7 — real submission (needs KUDPC access + ssh key)

First run `verdi computer test sp`. Then submit, watch with
`verdi process list` / `verdi process report`, and collect
`output_parameters`, `output_structure` and `energy_ev`.

This settles roadmap risk #1 (`g16 < aiida.inp` over stdin). If stdin does
not work, register a thin wrapper as the code's executable — a one-line
change in `config/site.yaml`.

Prerequisite: the `./run.py up` session must stay alive while the job runs.

### Step 8 — documentation

README section covering the two config files, the calculation format, and
the dry-run-first workflow.

## Acceptance gate

Phase 1 is complete only when all of these hold (from the roadmap):

1. The image build asserts the `gaussian` calculation and `gaussian.base`
   parser entry points.
2. `tools/setup_site.py` is idempotent: a second run preserves the Computer
   and Code identities.
3. The automated dry-run test shows the exact KUDPC resource shape and quoted
   `srun g16` command, with `%nprocshared` equal to the allocated cores and
   `%mem` equal to the documented 75% memory budget.
4. With cluster access, `verdi computer test sp` passes.
5. A real H2O job finishes, parses, and exposes `output_parameters`,
   `output_structure` and `energy_ev`.
6. The calculation is findable from a fresh `verdi process list`/query, and
   its input nodes, retrieved output log and provenance are inspectable
   without ad-hoc work-unit directories.

## Who does what

| step | executor |
| --- | --- |
| 1–6, 8 | local work — buildable without cluster access |
| 7 and acceptance gates 4–6 | need the real environment: KUDPC account, `~/.ssh/id_kudpc`, network access, and the real `config/site.yaml` / `config/binds.conf` |

## Explicitly out of scope

Slurm arrays, TOML config, a generic settings framework, any Phase 2
WorkChain, NOMAD, and validation of multi-node `--rsc` semantics. The
single-node H2O slice proves the generated shape but cannot distinguish
competing interpretations of `p` and `m`; settle that before the first
multi-node calculation. All belong to later phases of
[`docs/gaussian-plan.md`](../gaussian-plan.md).
