# aiida-slurm-rsc

AiiDA scheduler plugin for **KUDPC Camphor (`sp`)**, registered as the
`slurm_rsc` entry point.

The cluster forbids the standard Slurm resource options — `--nodes`,
`--ntasks*`, `--cpus-per-task`, `--mem`, `--qos` — and wants its own spec
instead:

```bash
#SBATCH --rsc p=1:t=16:c=16:m=16G
```

so aiida-core's stock `core.slurm` scheduler cannot submit there at all. This
plugin subclasses `SlurmScheduler` and reimplements only the submit-script
header; `squeue`/`sacct` parsing is inherited unchanged.

It is installed into the image at build time (see the repository README).

## Resources

Calculations keep declaring ordinary AiiDA resources. The mapping is:

| AiiDA | `--rsc` |
| --- | --- |
| `num_machines` | `p` |
| `num_mpiprocs_per_machine × num_cores_per_mpiproc` | `t` and `c` |
| `max_memory_kb` | `m`, rounded **up** to whole GiB; omitted when unset |

```python
builder.metadata.options.resources = {"num_machines": 1, "num_mpiprocs_per_machine": 4}
builder.metadata.options.max_memory_kb = 4 * 1024 * 1024
builder.metadata.options.queue_name = "gr10641a"
#   -> #SBATCH --rsc p=1:t=4:c=4:m=4G
#      #SBATCH --partition=gr10641a
```

`metadata.options.priority` becomes `--nice=`, which is how the group's
`slurm-async-runner` pipeline pushed heavy jobs (CREST, Gaussian arrays) behind
everything else on the shared queue. AiiDA emits nothing when it is unset, so
there is no equivalent of that pipeline's default `--nice=1000`: set it per
calculation if you want the same manners.

`account` and `qos` are accepted by AiiDA and **silently dropped** here — KUDPC
rejects both.

## Mail notification

aiida-core has no option for this. `CalcJob.presubmit` leaves
`JobTemplate.email` unset (the line that would fill it is commented out) and
`metadata.options` is a static port namespace a scheduler plugin cannot extend.
What aiida-core does offer is `metadata.options.custom_scheduler_commands`, a
per-calculation string appended verbatim to the header — the supported way to
reach `sbatch` with something AiiDA has no opinion about. `mail_scheduler_commands()`
renders it:

```python
from aiida_slurm_rsc import mail_scheduler_commands

# an intermediate step: report its own failure (the default)
builder.metadata.options.custom_scheduler_commands = mail_scheduler_commands(computer)
#   -> #SBATCH --mail-user=you@example.org
#      #SBATCH --mail-type=FAIL

# the last step: one "it finished" mail as well
builder.metadata.options.custom_scheduler_commands = mail_scheduler_commands(
    computer, terminal=True
)
#   -> #SBATCH --mail-type=END,FAIL
```

That FAIL-everywhere / END+FAIL-at-the-end split is the policy the
`slurm-async-runner` pipeline used: every step reports its own failure, and
"the work is done" is a single message from the terminal step rather than one
per step.

### Where the address comes from

It belongs to the cluster account rather than to any one calculation, so it is
stored once on the **`Computer`**, in aiida-core's own free-form per-computer
metadata:

```console
$ ./run.py attach verdi shell
>>> computer = load_computer("sp")
>>> computer.set_property("mail_user", "you@example.org")
>>> computer.store()          # if it was loaded unstored
```

`mail_scheduler_commands()` takes either that computer or a plain address
string. **A computer without the property mails nobody**, so notification is
opt-in and no address is committed to this repository.

Per calculation, `mail_types=` overrides the policy outright
(`mail_types=["BEGIN", "END"]`), and `mail_types=["NONE"]` opts one calculation
out. An unknown mail type raises at submission rather than being passed on: a
notification that never arrives looks exactly like nothing having gone wrong,
so it has to fail loudly.

## Tests

Header generation and the mail helper. No cluster, no profile, no daemon.
pytest is installed in the image.

```console
$ ./run.py attach bash -c 'cd /opt/aiida_plugins/aiida-slurm-rsc && python3 -m pytest'
```

To exercise a working copy before rebuilding the image, put it first on the
path:

```console
$ ./run.py attach env PYTHONPATH=$PWD/aiida_plugins/aiida-slurm-rsc \
      python3 -m pytest aiida_plugins/aiida-slurm-rsc/tests
```
