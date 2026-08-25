# Roadmap: from one Gaussian job to reusable AiiDA workflows

Status: **proposed roadmap** — the container and `aiida-slurm-rsc` exist; the
Gaussian and workflow phases below have not been implemented.

The first deliverable is deliberately small: submit and parse one H2O geometry
optimisation on KUDPC Camphor. It is a **vertical slice**, not the architecture.
It must prove the container, AiiDA daemon, SSH transport, `slurm_rsc`,
`aiida-gaussian`, Gaussian environment and retrieval path together.

The target is a native AiiDA replacement for new calculations now run by
`structual_search`: first the core CREST → preliminary DFT → filter → refined
DFT/frequency → ranking pipeline, then its specialised branches. The old
pipeline stays usable until equivalent AiiDA stages pass behavioural parity
checks. AiiDA will not launch the old submit scripts as its long-term workflow
engine.

This roadmap also fixes the package and data boundaries needed for other
scientific workflows and a later NOMAD Oasis deployment. It does **not** build a
universal DFT abstraction, package speculative plugins, migrate every old
work-unit, or install NOMAD now.

## Decisions

- This repository is the **deployment and integration layer**: Apptainer,
  service lifecycle, AiiDA profile setup, site configuration and runnable
  examples.
- Upstream `aiida-gaussian` remains the Gaussian CalcJob/parser provider.
- `aiida_plugins/aiida-slurm-rsc` remains an independent scheduler plugin and
  must not acquire chemistry or workflow logic.
- A future `aiida-structural-search` plugin will own migrated scientific logic,
  CREST integration and the structure-search WorkChains. Logic is moved from
  `structual_search` stage by stage; the old package is retired only after
  parity.
- Start with that one scientific plugin. Extract a generic `aiida-crest` only
  when a second real consumer needs it.
- Molecular and periodic DFT are separate workflow families. They share this
  deployment/site layer and AiiDA data types, but are not forced through one
  common DFT WorkChain.
- Typed AiiDA process inputs and versioned protocol presets are authoritative.
  YAML calculation files are thin launch adapters, not a second workflow
  engine. Site and credential configuration stays separate from scientific
  inputs.
- Each conformer is one CalcJob, initially with bounded workflow concurrency.
  Slurm array support is deferred until measured scheduler pressure justifies
  its coarser provenance and extra code.
- Individual conformer failures do not necessarily fail the whole WorkChain.
  A run succeeds if at least one scientifically valid result remains, while
  failed/skipped children and reasons are emitted as structured outputs.
- AiiDA is the source of truth for execution and provenance. NOMAD later
  receives selected completed datasets in a one-way publication flow; it is
  not a bidirectional mirror of the AiiDA database.

## Target boundaries and flow

```mermaid
flowchart LR
  launchYaml["YAML launch adapter"] --> workflow["aiida-structural-search WorkChain"]
  workflow --> crest["CREST CalcJob"]
  crest --> prelim["Gaussian preliminary CalcJobs"]
  prelim --> filter["Filtering calculation"]
  filter --> refine["Gaussian refine and frequency CalcJobs"]
  refine --> result["Ranked structures and failure report"]
  result -. "future selected export" .-> nomad["NOMAD Oasis"]
  siteConfig["Site and credential config"] --> platform["AiiDA container and profile"]
  platform --> workflow
```

The plugin boundary follows responsibility, not executable count:

| component | owns | must not own |
| --- | --- | --- |
| this repository | image composition, runtime wrapper, profile/site setup, examples | chemistry algorithms |
| `aiida-slurm-rsc` | KUDPC `#SBATCH --rsc` and scheduler mail rendering | Gaussian/CREST policy |
| upstream `aiida-gaussian` | Gaussian input, CalcJob, parser and base restart WorkChain | structure-search orchestration |
| future `aiida-structural-search` | CREST integration, filtering/ranking logic, protocols and WorkChains | site credentials or KUDPC scheduler syntax |
| future workflow plugins | other molecular or periodic calculations | changes to the shared platform unless genuinely required |
| future NOMAD exporter | selected AiiDA result → NOMAD upload mapping | job execution or bidirectional synchronisation |

This separation also makes future GitHub publication mechanical: every local
plugin is already a Python distribution with tests and entry points, while the
deployment repository consumes released versions. During development it may
continue to build local plugin directories into the image.

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

## Phase 1 design: one Gaussian vertical slice

Phase 1 proves the smallest useful end-to-end path. It does not add
`aiida-structural-search`; there is no workflow logic to package yet.

### Configuration layout

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

### Implementation steps

#### 1. Bake `aiida-gaussian` into the image

Add it to `%post` in `containerfiles/aiida.def` next to `aiida-slurm-rsc`, with
the same entry-point assertion. A runtime `pip install` is not an option for the
same reason as before: `/home/aiida/.local` belongs to the read-only image and a
`--user` install would land in the tmpfs overlay. pymatgen and ase add a few
hundred MB to the image.

Rebuild: `./run.py down` → `apptainer build --fakeroot --force` → `./run.py up`.

#### 2. Teach `run.py` about `config/binds.conf`

Parse the file if it exists, expand `~`, fail loudly on a missing source rather
than letting Apptainer create an empty directory, and append to `BINDS`. No
file, no extra mounts — the default session still carries no credentials.

#### 3. `tools/setup_site.py` — computer and code from `config/site.yaml`

Idempotent: create or update the `Computer`, configure the transport, set
`mail_user`, then create or update each `InstalledCode`. Run as
`./run.py attach python3 tools/setup_site.py` (the repository is visible inside
the container — Apptainer binds the working directory). Finish with
`verdi computer test <label>`.

#### 4. `tools/submit.py` — a calculation file to a submitted job

`./run.py attach python3 tools/submit.py examples/h2o_opt.yaml [--dry-run]`.
Builds the `gaussian` builder from the file, derives the link0 block from the
resources, applies the mail policy through `custom_scheduler_commands`, and
either submits or dumps the dry-run folder.

#### 5. Dry-run before touching the cluster

`--dry-run`, then read `_aiidasubmit.sh` and `aiida.inp`. Check that:

- `#SBATCH --rsc p=1:t=4:c=4:m=4G` is present;
- **none** of `--nodes`, `--ntasks*`, `--cpus-per-task`, `--mem`, `--qos` appear;
- the command line is `srun 'g16' < 'aiida.inp' > 'aiida.out'`;
- `%nprocshared` and `%mem` match the `--rsc` line.

This is the first check of `slurm_rsc` against a real calculation rather than a
hand-built `JobTemplate`.

#### 6. Submit, watch, collect

`verdi process list` / `verdi process report`, then `output_parameters`,
`output_structure` and `energy_ev`. The H2O calculation itself is minutes; the
queue is the slow part.

#### 7. Document it

A README section covering the two config files, the calculation format, and the
dry-run-first workflow. Add `config/site.yaml` and `config/binds.conf` to
`.gitignore`.

### Phase 1 acceptance gate

Phase 1 is complete only when all of these hold:

1. Image build asserts that the `gaussian` calculation and
   `gaussian.base` parser entry points are installed.
2. `tools/setup_site.py` is idempotent and `verdi computer test sp` passes.
3. The dry run has the exact KUDPC resource shape and `srun g16` command listed
   above, with matching Slurm and Gaussian CPU/memory values.
4. A real H2O job finishes, parses, and exposes `output_parameters`,
   `output_structure` and `energy_ev`.
5. The calculation can be found from a fresh `verdi process list`/query and its
   input nodes, retrieved output log and provenance can be inspected without
   relying on an ad-hoc work-unit directory. Generated `aiida.inp` and
   `_aiidasubmit.sh` are checked in the dry-run folder under item 3; Phase 1
   does not assume that AiiDA permanently retrieves submission files.

## Phase 2: native core structure-search workflow

Create `aiida-structural-search` only when Phase 1 works. Its first target is
the old pipeline's main path:

1. Run CREST as a CalcJob and parse its conformer ensemble.
2. Fan out one upstream `GaussianBaseWorkChain` per conformer for preliminary
   optimisation.
3. Wait for every child to reach a terminal state, retain successful results,
   and run the migrated RMSD/energy filter as an AiiDA process.
4. Fan out refine/frequency calculations for survivors, using parent remote
   folders/checkpoints only through explicit provenance links.
5. Rank valid results by E+ZPE and emit at least:
   - ranked structures and energies;
   - the selected best structure;
   - survivor/cluster mapping;
   - failed and excluded conformers with machine-readable reasons;
   - protocol name/version and effective parameters.

The WorkChain controls fan-out concurrency. It continues after individual
preliminary or refine failures and finishes successfully when at least one
valid ranked conformer remains. No survivors is a workflow failure with a
specific exit code.

### Scientific configuration

WorkChain ports define charge, multiplicity, structures, code choices and
resource inputs. Versioned protocols define the old preliminary and refine
methods. A YAML adapter may select a protocol and override exposed values, but
the adapter must build an ordinary process builder; scientific defaults do not
live in the CLI.

Resource allocation and Gaussian `%nprocshared`/`%mem` continue to derive from
one validated source. Site defaults may supply queue and transport details but
must not silently change the scientific method.

### Phase 2 acceptance gate

Freeze a small, redistributable input and the legacy pipeline's expected
outputs. Run both implementations and compare:

- number and identities of generated conformers where deterministic;
- preliminary completion classification;
- RMSD clusters and survivor set;
- final structures, energies and E+ZPE ranking within documented tolerances;
- partial-failure behaviour.

Unit tests cover migrated pure logic; process tests cover provenance and
failure paths; a KUDPC integration run covers real executables. Retire a legacy
stage only after its parity check passes. Exact scheduler job IDs, timestamps
and floating-point text formatting are not parity requirements.

## Phase 3: migrate specialised legacy paths

After the core path is stable, migrate specialised paths in response to actual
use: solvated refine, UV/Vis, dock/seed generation, orientation scan and
NEB/TS/IRC. Reuse the same Gaussian process and AiiDA data outputs; add separate
WorkChains where the scientific lifecycle differs. Do not turn the core
WorkChain into a flag-driven copy of every legacy branch.

Each path gets its own frozen fixture and parity gate. Old completed
calculations are not rewritten as successful CalcJobs. Selected historical
inputs and outputs may be imported as clearly labelled external `Data` nodes
and grouped for comparison or publication.

## Phase 4: other DFT workflows

Add molecular or periodic engines through their maintained AiiDA plugins when
possible. A future ORCA-like molecular workflow and a Quantum ESPRESSO
periodic workflow may reuse structures, groups, deployment and publication
conventions, but each owns its scientific protocols and outputs. Introduce a
shared interface only after two implemented workflows demonstrate the same
contract.

## Production deployment gate: service lifecycle and KUDPC hosting

For Phase 1, this Manjaro host may run the existing all-in-one Apptainer
session with `./run.py up`. Before multi-hour production workflows, the AiiDA
services must have an observable, restartable owner and documented backup and
recovery procedure; an interactive shell or remembered background process is
not sufficient.

The intended later host is KUDPC itself, subject to site policy. Moving there
is a deployment gate: confirm long-lived process/container permission,
filesystem locations, network ports, SSH/loopback access, daemon restart
behaviour and backups before treating it as production. Complete this gate
before using Phase 2 for unattended production; it can be developed in
parallel with the Phase 1 and Phase 2 code. This does not require changing
scientific WorkChains.

## Phase 5: publish selected results to NOMAD Oasis

NOMAD is a separate service stack and is not added to the AiiDA image. AiiDA
remains authoritative for calculations and provenance. The publication path
selects completed AiiDA groups/nodes and emits a NOMAD-supported upload
containing raw inputs/outputs plus mapped searchable metadata and stable AiiDA
identifiers.

An AiiDA `.aiida` archive may accompany an upload for provenance preservation,
but NOMAD is not assumed to parse it. Start with an explicit exporter and one
dataset; add a NOMAD parser/schema plugin only if the supported raw formats and
archive YAML/JSON cannot represent the required metadata. There is no
bidirectional synchronisation in this roadmap.

### Phase 5 acceptance gate

A selected result can be traced from its NOMAD entry back to immutable AiiDA
identifiers, its method/structure/energy are searchable in NOMAD, raw files are
downloadable, and repeating publication is idempotent or detects duplicates.

## Cross-phase risks and decisions to verify

1. **`g16 < aiida.inp` is unverified on KUDPC.** The old pipeline always passed
   the input as an argument (`srun g16 file.gjf`). aiida-gaussian cannot do
   that. If stdin turns out not to work, the fix is a thin wrapper on the
   cluster that spools stdin to a file and calls `g16` with it, named as the
   code's executable instead of `g16` — a one-line change in `config/site.yaml`,
   which is the point of keeping it there. The first submission settles this.
2. **The session has to stay up.** The daemon dies with the current `run.py`
   session, so `./run.py up` must outlive the job. This is acceptable for the
   H2O gate only; the production deployment gate blocks unattended migration.
3. **`work_dir` shares a filesystem with the old pipeline but not a directory.**
   AiiDA creates a wide hash tree under its work_dir; `calc_temp` is its own
   directory so the two layouts do not interleave.
4. **`aiida-gaussian` retrieves `aiida.out`, not Gaussian checkpoints.** Its
   `parent_calc_folder` exposes a completed remote directory to a child, which
   can support checkpoint-based continuation while that directory exists.
   Phase 2 must verify the exact `%oldchk` layout and define remote-cleanup and
   final-checkpoint retention policy before depending on it.
5. **One CalcJob per conformer may pressure the scheduler.** Begin with bounded
   concurrency because it gives clean provenance and per-conformer retry.
   Measure realistic ensemble sizes and queue behaviour before implementing
   Slurm arrays.
6. **The current image uses Python 3.10 while `structual_search` declares
   Python 3.11+.** Migrated logic must either be made/tested compatible with
   the image or wait for a deliberate base-image upgrade. Do not silently add
   the old package as an image dependency.
7. **Upstream plugin capability is a constraint, not an assumption.** Verify
   Link1, solvation, UV/Vis, restart and parser needs against
   `aiida-gaussian` before each specialised migration. Prefer an upstream
   contribution; add local integration only for a demonstrated gap.
8. **Partial success needs an explicit scientific threshold.** “At least one
   valid result” is the initial rule. A specialised workflow may require a
   stronger completeness threshold, but it must expose that policy and the
   excluded children.
9. **Legacy and AiiDA results may differ without either being wrong.** Pin
   executable/module versions and protocol inputs in parity fixtures, compare
   numeric results with scientific tolerances, and document accepted
   differences.
10. **Credentials and licensed software remain external.** Bind only required
    SSH/licence files; Gaussian stays on the authorised remote system and is
    never baked into or published with the image.
