# Plan: running Gaussian from this container

Status: **proposed** — nothing below has been implemented yet.

The worked example is a geometry optimisation of H2O, but H2O is only the
example: the calculation, the cluster and the credentials are all described by
configuration files, none of which are baked into the code.

## Where it has to run

Neither the host nor the image has Gaussian, and neither has `sbatch`. The only
place this can run is a real cluster — for us **KUDPC Camphor (`sp`)**, which
makes this the first real use of the `slurm_rsc` scheduler plugin.

Current state of the profile: computer `localhost` only, no codes, and
`aiida.calculations` holds nothing but the five core entry points.

## What the old pipeline already established

Carried over from `structual_search`'s `pipeline/` — all of it site-specific,
so all of it belongs in configuration rather than in code:

| | |
| --- | --- |
| partition | `gr10641a` |
| a typical Gaussian allocation | `p=1:t=16:c=16:m=16G`, 6 h |
| Gaussian environment | `. /usr/share/Modules/init/bash` then `module restore gaussian_A -f` |
| scratch | `GAUSS_SCRDIR` under `$SLURM_TMPDIR` |
| launch | **`srun g16 …`** — KUDPC requires the job's program to be started by `srun` |

## The plugin

`aiida-gaussian` 2.1.0 from PyPI: entry point `gaussian`, parser
`gaussian.base`, dependencies `pymatgen`, `cclib<=2.0`, `ase`. It runs the code
as `g16 < aiida.inp > aiida.out` — **stdin is hardcoded**, `settings.cmdline`
only adds arguments — and defaults `withmpi` to False.

## Configuration layout

Two committed example files, two git-ignored real ones. Nothing site-specific
is committed, so the repository is usable by someone with a different cluster,
a different account and a different Gaussian module.

```
config/site.example.yaml     committed — placeholders and comments
config/site.yaml             git-ignored — the real cluster, code and mail settings
config/binds.example.conf    committed
config/binds.conf            git-ignored — extra Apptainer bind mounts (ssh key, …)
examples/h2o_opt.yaml        committed — one calculation, swappable
examples/h2o.xyz             committed
```

Two files rather than one because they have different readers. `run.py` is
deliberately standard-library-only and runs on the host *before* AiiDA exists,
so it gets a format it can parse in five lines. `config/site.yaml` is read
inside the container, where aiida-core guarantees PyYAML — and where the
container's Python 3.10 rules out `tomllib`, which is why this is YAML and not
TOML.

### `config/binds.conf` — credentials stay out of the image and out of git

One `src:dst[:ro]` per line, `#` comments, and **no bind by default**. `run.py`
appends whatever is listed to its own bind list, for `up`, `shell` and `attach`
alike. For ssh access to a cluster:

```
# ssh material for the remote computer, read-only
~/.ssh/id_kudpc:/home/aiida/.ssh/id_kudpc:ro
~/.ssh/known_hosts:/home/aiida/.ssh/known_hosts:ro
```

Individual files, not the whole `~/.ssh`. The mechanism is generic — it is a
bind list, not an "ssh option" — so it also covers a licence file or a
structure library later.

This cannot be an AiiDA setting: `run.py` has to know the mount before the
container starts, and the profile it would have to ask lives in a PostgreSQL
instance inside that container.

### `config/site.yaml` — the cluster, the code, the mail address

```yaml
computer:
  label: sp
  hostname: camphor.kudpc.kyoto-u.ac.jp
  scheduler: slurm_rsc
  transport: core.ssh
  work_dir: /LARGE0/gr10641/aiida/calc_temp/{username}
  mpirun_command: [srun]          # with withmpi=True this yields `srun g16`
  default_queue: gr10641a
  ssh:
    username: <your cluster account>
    key_filename: /home/aiida/.ssh/id_kudpc   # the *container* path from binds.conf
    safe_interval: 60
  mail_user: ""                   # empty = no notification (see the plugin README)

codes:
  - label: g16
    executable: g16
    plugin: gaussian
    prepend_text: |
      . /usr/share/Modules/init/bash
      module restore gaussian_A -f
      export GAUSS_SCRDIR="${SLURM_TMPDIR:-/tmp}/g16_${SLURM_JOB_ID:-$$}"
      mkdir -p "${GAUSS_SCRDIR}"
```

`mpirun_command: [srun]` passes aiida-core's validator — a command with no
`{tot_num_mpiprocs}` placeholder is accepted — and reproduces the old
pipeline's `srun g16`. `safe_interval` is raised from the default 30 s so the
daemon does not hammer the login node. `mail_user` ends up as a `Computer`
property, which is what the `slurm_rsc` mail helper already reads; empty means
nobody is mailed, so no address is ever committed.

**Our own `config/site.yaml`** fills in `work_dir:
/LARGE0/gr10641/aiida/calc_temp/{username}` and the KUDPC account. The
committed example keeps placeholders.

### A calculation file — H2O is just one of them

```yaml
code: g16@sp
structure: examples/h2o.xyz     # anything ASE reads
charge: 0
multiplicity: 1

functional: B3LYP
basis_set: 6-31G(d)
route_parameters:
  opt: null                     # `freq` only once a plain opt has gone through

resources:
  num_machines: 1
  num_mpiprocs_per_machine: 1
  num_cores_per_mpiproc: 4
memory_gb: 4
max_wallclock_seconds: 1800
queue: gr10641a                 # optional; falls back to site default_queue
mail: default                   # default | terminal | none | [BEGIN, END]
```

Swapping the calculation means writing another one of these — a different
structure, route, charge or size. Nothing about H2O is in the code.

> **`%nprocshared` and `%mem` do not follow the AiiDA resources.**
> aiida-gaussian writes `link0_parameters` verbatim, so a mismatch between the
> Gaussian input and the Slurm allocation is silent. The submitter derives
> both `--rsc` and the link0 block from the `resources`/`memory_gb` above, so
> there is one source for the pair.

ASE reads the structure; `StructureData` gets `pbc=(False, False, False)` and a
bounding-box cell, because aiida-gaussian calls `get_pymatgen_molecule()`,
which refuses a periodic structure.

## Steps

### 1. Bake `aiida-gaussian` into the image

Add it to `%post` in `containerfiles/aiida.def` next to `aiida-slurm-rsc`, with
the same entry-point assertion. A runtime `pip install` is not an option for the
same reason as before: `/home/aiida/.local` belongs to the read-only image and a
`--user` install would land in the tmpfs overlay. pymatgen and ase add a few
hundred MB to the image.

Rebuild: `./run.py down` → `apptainer build --fakeroot --force` → `./run.py up`.

### 2. Teach `run.py` about `config/binds.conf`

Parse the file if it exists, expand `~`, fail loudly on a missing source rather
than letting Apptainer create an empty directory, and append to `BINDS`. No
file, no extra mounts — the default session still carries no credentials.

### 3. `tools/setup_site.py` — computer and code from `config/site.yaml`

Idempotent: create or update the `Computer`, configure the transport, set
`mail_user`, then create or update each `InstalledCode`. Run as
`./run.py attach python3 tools/setup_site.py` (the repository is visible inside
the container — Apptainer binds the working directory). Finish with
`verdi computer test <label>`.

### 4. `tools/submit.py` — a calculation file to a submitted job

`./run.py attach python3 tools/submit.py examples/h2o_opt.yaml [--dry-run]`.
Builds the `gaussian` builder from the file, derives the link0 block from the
resources, applies the mail policy through `custom_scheduler_commands`, and
either submits or dumps the dry-run folder.

### 5. Dry-run before touching the cluster

`--dry-run`, then read `_aiidasubmit.sh` and `aiida.inp`. Check that:

- `#SBATCH --rsc p=1:t=4:c=4:m=4G` is present;
- **none** of `--nodes`, `--ntasks*`, `--cpus-per-task`, `--mem`, `--qos` appear;
- the command line is `srun 'g16' < 'aiida.inp' > 'aiida.out'`;
- `%nprocshared` and `%mem` match the `--rsc` line.

This is the first check of `slurm_rsc` against a real calculation rather than a
hand-built `JobTemplate`.

### 6. Submit, watch, collect

`verdi process list` / `verdi process report`, then `output_parameters`,
`output_structure` and `energy_ev`. The H2O calculation itself is minutes; the
queue is the slow part.

### 7. Document it

A README section covering the two config files, the calculation format, and the
dry-run-first workflow. Add `config/site.yaml` and `config/binds.conf` to
`.gitignore`.

## Risks

1. **`g16 < aiida.inp` is unverified on KUDPC.** The old pipeline always passed
   the input as an argument (`srun g16 file.gjf`). aiida-gaussian cannot do
   that. If stdin turns out not to work, the fix is a thin wrapper on the
   cluster that spools stdin to a file and calls `g16` with it, named as the
   code's executable instead of `g16` — a one-line change in `config/site.yaml`,
   which is the point of keeping it there. The first submission settles this.
2. **The session has to stay up.** `run.py` sessions are foreground
   (`docs/warnings.md`); the daemon dies with them, so `./run.py up` must
   outlive the job. Fine for H2O, not for the multi-hour jobs that come later.
3. **`work_dir` shares a filesystem with the old pipeline but not a directory.**
   AiiDA creates a wide hash tree under its work_dir; `calc_temp` is its own
   directory so the two layouts do not interleave.
