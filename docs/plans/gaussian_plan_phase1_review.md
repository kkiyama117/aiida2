# Review: `docs/plans/gaussian_plan_phase1.md`

Reviewed 2026-08-26 against the actual repository, the running container
(aiida-core 2.9.0, Python 3.10.13, numpy 2.2.6) and `aiida-gaussian` 2.2.0
from PyPI. Every claim below marked *verified* was checked by running it.

**Status: resolved.** The revised plan (commit `a3b9bda`) addresses every item
below, and Phase 1 is approved for implementation. See the addendum at the end
for the two follow-ups: a correction to this review's own memory advice, and
one outstanding documentation inconsistency.

## Verdict

The structure and the ordering are sound — image → binds → config → setup →
submit → dry run → real submission — and putting the dry run before any
cluster access is the right call. Two things block it as written: the plan
never sets `withmpi`, so acceptance criterion 3 cannot pass, and the Step 1
dependency caveat is the opposite of what actually happens.

---

## 1. Blocking: `srun` is never emitted

Nothing in the plan sets `metadata.options.withmpi = True`.

`aiida-gaussian` 2.2.0 overrides the port with a `False` default and feeds it
straight into the code info:

```python
spec.input("metadata.options.withmpi", valid_type=bool, default=False)
...
codeinfo.withmpi = self.inputs.metadata.options.withmpi
```

`CalcJob.presubmit` (aiida-core 2.9.0) reconciles three values — the *raw*
`metadata.options.withmpi` input, `codeinfo.withmpi`, and `code.with_mpi` —
and only applies `mpirun_command` when the result is true. With the option
left unset the result is `False`, `prepend_cmdline_params` stays empty, and
`mpirun_command: [srun]` in `config/site.yaml` has no effect whatsoever.

**Fix:** Step 5 must state that `submit.py` sets
`builder.metadata.options.withmpi = True`. Set it in exactly one place — the
three-way reconciliation raises on a mismatch.

*Verified:* `[srun]` passes aiida-core's substitution
(`mpi_args = [arg.format(tot_num_mpiprocs=...) for arg in computer.get_mpirun_command()]`),
so a command with no placeholder is fine.

## 2. Factual corrections

### (a) The Step 1 dependency caveat is inverted — lines 32-35

Resolved inside the running image:

```console
$ pip install --user --dry-run aiida-gaussian
aiida-core : Requirement already satisfied   # NOT reinstalled
numpy 2.2.6: untouched
Would install: aiida-gaussian-2.2.0 ase-3.29.0 cclib-1.8.1 pymatgen-2025.10.7
               scipy matplotlib pandas plotly sympy spglib monty networkx …
               (31 packages)
```

- A plain resolve does **not** pull `aiida-core` back in: 2.9.0 in the user
  site already satisfies `aiida-core<3.0.0,>=2.0.0`.
- The proposed `--no-deps` + explicit `pymatgen`/`ase`/`cclib` recipe would
  **break the install**: pymatgen itself needs monty, spglib, scipy, sympy,
  pandas, matplotlib and more, and none of them would be installed.
- `--no-deps` is right for `aiida-slurm-rsc` only because that plugin depends
  on nothing but aiida-core. It does not generalise here.

Rewrite the caveat around what is actually at risk: ~31 transitive packages
and several hundred MB of image growth, none of which is version-pinned.

### (b) Version drift — the definition file's own rule is not applied

`docs/gaussian-plan.md` says "aiida-gaussian 2.1.0"; the current release is
**2.2.0**. More importantly Step 1 does not pin anything, while
`containerfiles/aiida.def` opens by pinning the base image *by digest* and
asserting every patch. Because the image is Python 3.10, pip silently
back-solves pymatgen to 2025.10.7 (the latest, 2026.5.4, requires >= 3.11) —
a future rebuild will move without warning.

**Fix:** install `aiida-gaussian==2.2.0` and pass a constraints line holding
`aiida-core==2.9.0` and `numpy==2.2.6`, so a replacement fails the build
instead of shipping quietly.

### (c) The expected run line is mis-quoted — line 86

`Scheduler._get_run_line` escapes `prepend_cmdline_params` too, so the
generated line is

```
'srun' 'g16' < 'aiida.inp' > 'aiida.out'
```

`srun` is quoted. If Step 6 is to be greppable, the expected string has to
match.

### (d) `safe_interval` is in the wrong place, and transport configuration is missing — lines 63-70

`safe_interval` is a transport (authinfo) parameter set by
`verdi computer configure`, not a `Computer` attribute. `docs/gaussian-plan.md`
has it correctly under `ssh:`; the Phase 1 step folded it into the Computer
bullet.

The more serious omission: Step 4's bullet list has **no transport
configuration step at all** (the roadmap's step 3 does have it). Without it
the `verdi computer test sp` on the next line cannot run.

### (e) `default_queue` has nowhere to live

aiida-core 2.9's `Computer` has no default queue (`get_default_mpiprocs_per_machine`
and `get_default_memory_per_machine` exist; no queue equivalent) — *verified*.
So "calculation `queue` falls back to the site `default_queue`" forces
`submit.py` to read `config/site.yaml` as well, which Step 5 does not mention.

Cleaner and consistent with the existing design: store it as
`computer.set_property("default_queue", …)`, the same mechanism the
`slurm_rsc` mail helper already uses for `mail_user`. `default_memory_per_machine`
can carry the memory default the same way.

### (f) The "who does what" table contradicts Step 4 — lines 125-128

The table claims steps 1–6 need no cluster access, but Step 4 ends with
`verdi computer test sp` and acceptance criterion 2 repeats it. That command
requires a working SSH connection to KUDPC.

**Fix:** split Step 4 into "create/update Computer and Codes, verify
idempotency" (local) and "`verdi computer test sp`" (same prerequisites as
Step 7).

## 3. Design concerns

### (g) `%mem` must not equal the `--rsc` `m` value

Acceptance criterion 3 asks for "matching Slurm and Gaussian CPU/memory
values". That is correct for CPUs and wrong for memory: Gaussian's `%mem`
covers dynamic memory only — static memory, the executable image and buffers
sit on top. Requesting `%mem=4GB` inside an `m=4G` allocation invites an OOM
kill. Convention is 70–80% of the allocation.

State the derivation as a rule (`%nprocshared = t`, `%mem = m − margin`) and
make *that* rule the thing the dry run checks.

### (h) Reject `num_mpiprocs_per_machine > 1`

`%nprocshared` is shared-memory parallelism. With `withmpi=True` and more
than one MPI process per machine, `srun` launches several `g16` processes,
each claiming `%nprocshared` threads. `submit.py` should validate
`num_mpiprocs_per_machine == 1` and fail loudly — Gaussian is not an MPI
program. This follows directly from the plan's own "one source for the pair"
principle.

### (i) Phase 1 cannot validate the `--rsc` semantics

`aiida-slurm-rsc` maps `p = num_machines`, `t = c = total cores`,
`m = total memory`. KUDPC's `--rsc` may instead mean *processes /
threads per process / cores per process / memory per process*. For the H2O
example (1 × 1 × 4) **both readings produce the identical string**, so
neither Step 6 nor Step 7 can tell them apart — and the plugin's own tests
only cover `p=1`.

Fine to leave out of scope, but say so explicitly: "Phase 1 does not verify
the meaning of `p` and `m`." Otherwise the first multi-node calculation
submits a silently wrong allocation.

### (j) The SSH surface in `site.yaml` is too thin

Missing: `key_policy` (aiida's default is `RejectPolicy`), `proxy_command`,
`port`, `look_for_keys`, `allow_agent`, `timeout`. Since `known_hosts` is
bound read-only, the host key **must** be registered on the host beforehand —
worth stating in Step 2/3. A site reached through a gateway will need
`proxy_command`.

## 4. Gaps

- **`submit_test/` pollutes the repository.** `_perform_dry_run` creates a
  `SubmitTestFolder` in the current working directory, and the container's
  cwd *is* the repository (*verified*: `./run.py attach pwd` →
  `/data/softwares/aiida2`). Add `submit_test/` to the `.gitignore` changes
  in Step 3.
- **A dry run is not `submit()`.** It needs `engine.run_get_node` with
  `metadata.dry_run = True`. One line in Step 5.
- **No tests.** This repository already bakes pytest into the image to test
  its plugin, yet Step 6 is "by eye or grep". At minimum: a unit test for the
  `binds.conf` parser (Step 2), and assertions over the generated
  `_aiidasubmit.sh` / `aiida.inp`. Make acceptance criterion 3 executable.
- **Step 2 needs one more guard.** Besides failing on a missing source,
  reject bind *destinations* under `/home/aiida/.local` and
  `/home/aiida/.aiida`: shadowing `.local` removes aiida-core itself.
- **Name the entry points to assert.** `gaussian` in `aiida.calculations`,
  `gaussian.base` in `aiida.parsers` (also `gaussian.cubegen`,
  `gaussian.formchk`, and `gaussian.base` in `aiida.workflows`). At that
  granularity the `%post` assertion writes itself.
- **`%chk` is undefined.** Without it in link0, Gaussian writes to the cwd.
  Deciding this now costs one line and helps roadmap risk #4 (`%oldchk`) in
  Phase 2; deliberately deferring it is also fine, but say which.

## 5. Verified as correct — leave as is

- **Acceptance criterion 4 matches the implementation.** `energy_ev` is a
  real output port (`Float`, `required=False`) and `output_structure` is
  emitted when `route_parameters` contains `opt` — which the example YAML
  does.
- **"Step 6 needs no cluster" is accurate.** `_perform_dry_run` uses only
  `LocalTransport`; it needs a stored `Computer` and `Code`, nothing more.
- **`./run.py attach python3 tools/setup_site.py` works as assumed** — the
  working directory is visible inside the container.
- Splitting configuration in two (`run.py` is stdlib-only; Python 3.10 has no
  `tomllib`) is well argued, and putting `mail_user` on the `Computer`
  meshes exactly with the existing `slurm_rsc` mail helper.
- Deferring roadmap risk #1 (`g16` over stdin) to Step 7, with a one-line
  `site.yaml` escape hatch if it fails, is the right shape.

---

**Priority:** 1 → 2(a)(b)(d)(f) → 3(g)(h) → 4. Items 1 and 2(a) must be
fixed before Step 1 and Step 6 can be executed at all.


---

# Addendum (2026-08-26)

## Correction: the 70–80% memory figure in §3(g) had no source

Section 3(g) above says "convention is 70–80% of the allocation", and the
revised plan turned that into `floor(0.75 * memory_gb)`. Asked for the source,
there isn't one. Recording what the primary and secondary sources actually
say, since the number was about to be frozen into an acceptance gate:

**Gaussian's own documentation prescribes no ratio at all.**
[Link 0 Commands](https://gaussian.com/link0/) says only that `%Mem` "Sets the
amount of dynamic memory used to N 8-byte words (default)", that "The default
memory size is 800 MB", and that `%NProcShared` "Requests that the job use up
to N processors/cores on shared memory parallel execution on SMP
multiprocessor computers".

**Published site guidance disagrees with itself** — on the number *and* on
what it is a fraction of:

| source | rule | measured against |
| --- | --- | --- |
| [Sigma2 / NRIS](https://documentation.sigma2.no/software/application_guides/gaussian/gaussian_resources.html) | under **80%** is "good practice" | node *physical* memory |
| [CU Boulder RC](https://curc.readthedocs.io/en/latest/software/gaussian.html) | **at least 50%** | the Slurm request (a *lower* bound) |
| [Princeton RC](https://researchcomputing.princeton.edu/support/knowledge-base/gaussian) | **at least 1 GB less** | the Slurm request (fixed offset) |
| [CSUC](https://confluence.csuc.cat/pages/viewpage.action?pageId=29362412) | **~75%** | the Slurm request |

(The Princeton and CSUC pages were not directly reachable — 403 and a TLS
error — so those two rows come from search snippets rather than from the pages
themselves.)

**A percentage is the wrong shape.** What the headroom pays for — the
executable image, static memory, per-thread stacks, I/O buffers — is roughly
constant, so a ratio mis-scales in both directions:

| allocation | 75% rule | headroom | |
| --- | --- | --- | --- |
| 2 GB | 1.5 GB | 0.5 GB | probably too little |
| 4 GB | 3 GB | 1 GB | reasonable |
| 16 GB | 12 GB | 4 GB | 3 GB wasted |
| 64 GB | 48 GB | 16 GB | 15 GB wasted, and paid for |

Princeton's fixed-offset form is the better one. The plan now uses
`%mem = memory_gb - 1` GiB (reject below 2 GiB), documents the constant as
provisional, and measures `MaxRSS` in Step 7 to replace convention with a
KUDPC number.

Changing the rule cost nothing: at the H2O example's 4 GB both rules give
`3 GB`, so Step 6's expected `%mem=3GB` and acceptance criterion 3 are
unaffected.

Two smaller points that came out of the same check:

- `%Mem`'s **default unit is 8-byte words**, so a bare `%mem=3` is 24 bytes,
  not 3 GB. `submit.py` must always emit an explicit unit. (`3GW` would be
  24 GB inside a 4 GB allocation — the same footgun in the other direction.)
- §3(g)'s description of the failure mode stands: an OOM-killed Gaussian
  writes no `Normal termination`, and `aiida-gaussian` reports that as exit
  code 391 `ERROR_NO_NORMAL_TERMINATION`, whose message reads "probably out of
  time". Memory exhaustion presents as a timeout.

## Outstanding: the roadmap is now stale

`docs/gaussian-plan.md` still carries the pre-revision Phase 1 design, and the
Phase 1 plan's acceptance gate cites it ("from the roadmap") while no longer
matching it:

| line | stale content |
| --- | --- |
| 109 | "aiida-gaussian 2.1.0" (current: 2.2.0) |
| 171 | `default_queue:` as a `Computer` field (no such field exists) |
| 260 | Step 3 ends with `verdi computer test <label>` (moved to Step 7) |
| 275 | `srun 'g16' …` — missing the quoting on `srun` |
| 276, 301 | "`%nprocshared` and `%mem` match the `--rsc` line" |
| 293-307 | the five-item acceptance gate (the plan now has six) |

Either update that section or mark it superseded by
`docs/plans/gaussian_plan_phase1.md`. This is a documentation fix; it does not
block implementation.
