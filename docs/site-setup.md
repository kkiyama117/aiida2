# Site setup: from clone to first calculation

A step-by-step guide for someone who has never used this repository. The end
state is one H2O Gaussian geometry optimisation submitted to your cluster
through AiiDA — checked with a dry run before anything touches the cluster.

Everything site-specific lives in two git-ignored files, so nothing below
leaks credentials into the repository:

| file | created in | what goes in it |
| --- | --- | --- |
| `config/binds.conf` | step 3 | which host files (SSH key) are mounted into the container |
| `config/site.yaml` | step 5 | cluster address, account, queue, code environment |

You need: Apptainer on this Linux machine, an account on the target cluster
(this guide assumes KUDPC Camphor), and the image built once:

```console
apptainer build --fakeroot containerfiles/aiida.sif containerfiles/aiida.def
```

## 1. Start the services

```console
./run.py up
```

This works without any credentials; leave it running — the AiiDA daemon that
later submits jobs lives in this session.

## 2. Prepare SSH material on the HOST

The container never sees your `~/.ssh` directory as a whole; you bind two
individual files into it. Generate a dedicated key for AiiDA:

```console
ssh-keygen -t ed25519 -f ~/.ssh/id_kudpc -N ""
```

`-N ""` = no passphrase. An automated daemon cannot type one; if your security
policy forbids passphrase-less keys, use `ssh-agent` and bind its socket too —
out of scope here.

Register the public half with the cluster (KUDPC: via the user portal's key
registration page). Then prove the login works **from the host** — this also
records the cluster's host key in your `~/.ssh/known_hosts`, which AiiDA's
default strict policy requires:

```console
ssh -i ~/.ssh/id_kudpc camphor.kudpc.kyoto-u.ac.jp
```

Answer `yes` once to accept the host key, confirm you get a shell, log out.
If this does not work, nothing later will.

> Use exactly the hostname above everywhere (it must match between
> `known_hosts`, `config/site.yaml`, and how you tested). If the center gives
> you a different login hostname, use that one consistently instead.

## 3. Bind the key into the container

```console
cp config/binds.example.conf config/binds.conf
$EDITOR config/binds.conf
```

Uncomment the two lines so the file reads:

```text
~/.ssh/id_kudpc:/home/aiida/.ssh/id_kudpc:ro
~/.ssh/known_hosts:/home/aiida/.ssh/known_hosts:ro
```

Left side: path on the host (`~` expands to your home). Right side: where it
appears inside the container — keep those paths, they are what
`config/site.yaml` refers to. `ro` = read-only inside the container.

**Restart the session** so the running container picks up the new mounts:

```console
./run.py down && ./run.py up
```

(Commands run through `./run.py attach` would see them immediately, but the
daemon doing the actual job uploads sits in the session started by `up`.)

Sanity check — the key must now exist inside the container:

```console
$ ./run.py attach head -1 /home/aiida/.ssh/id_kudpc
-----BEGIN OPENSSH PRIVATE KEY-----
```

## 4. Understand what you are about to configure

`config/site.yaml` tells AiiDA three things: *where* the cluster is
(hostname, work directory, queue), *how* to log in (the SSH parameters under
`ssh:`), and *what executable to run* (the `codes:` section, whose
`prepend_text` loads the Gaussian module on the compute nodes). Read
[`config/site.example.yaml`](../config/site.example.yaml) once — every line
has a comment. Optional SSH settings (gateway `proxy_command`, non-standard
port) are shown commented-out there; the direct KUDPC setup needs none of
them.

## 5. Fill in your real configuration

```console
cp config/site.example.yaml config/site.yaml
$EDITOR config/site.yaml
```

Minimum edits for KUDPC:

- `ssh.username:` — your cluster account name
- `work_dir:` — where AiiDA stages files on the cluster; `{username}` is
  substituted automatically, the default path follows the center's quota
  layout, only change the leading group area if yours differs
- `mail_user:` — optional; put your mail address there if you want Slurm
  notification, leave `""` for silence

Leave the rest as shipped unless you know why. Keep
`load_system_host_keys: true`: without that line the SSH transport never
reads `known_hosts`, and every connection fails with
`not found in known_hosts` no matter how correct your key setup is.

## 6. Create the AiiDA records

```console
$ ./run.py attach python3 tools/setup_site.py
computer sp: created (pk 12)
code g16@sp: created (pk 13)
```

Local database operations only — nothing contacts the cluster yet. Running it
a second time is safe and changes nothing (same PKs); after you edit
`site.yaml` it updates the records in place. If it says a code was *replaced*,
that is the intended response to a changed definition.

## 7. Verify the connection

```console
./run.py attach verdi computer test sp
```

This logs in over SSH, checks the scheduler and creates/deletes a scratch
directory. All checks should print `OK`. First real use of your credentials.

## 8. Dry-run the calculation, then submit

```console
$ ./run.py attach python3 tools/submit.py examples/h2o_opt.yaml --dry-run
dry-run folder: .../submit_test/20260826-00005
```

Open the two generated files: `_aiidasubmit.sh` must show
`#SBATCH --rsc p=1:t=4:c=4:m=4G` and `'srun' 'g16' < 'aiida.inp' > 'aiida.out'`,
`aiida.inp` must show `%nprocshared=4`, `%mem=3GB`, `%chk=aiida.chk`. This is
exactly what the automated test `tests/test_dry_run_integration.py` asserts;
if you changed `resources` or `memory_gb` in the YAML, the numbers move with
it (`%mem` = allocated memory − 1 GiB headroom).

Happy? Submit for real:

```console
$ ./run.py attach python3 tools/submit.py examples/h2o_opt.yaml
submitted calculation node pk=24
$ ./run.py attach verdi process list -a        # watch it
$ ./run.py attach verdi process report 24      # if something fails
```

The H2O job itself takes minutes; the queue is the slow part. Keep the
`./run.py up` session alive until then. Results afterwards:

```console
./run.py attach bash -c 'verdi process show 24 && verdi data core.dict list' # or:
./run.py attach verdi shell   # then: load_node(24).outputs.energy_ev.value
```

## Troubleshooting

| error (where it appears) | cause | fix |
| --- | --- | --- |
| `bind source does not exist: …` (`run.py`) | left side of a `binds.conf` line points at a missing file | fix the path; did step 2 create `~/.ssh/id_kudpc`? |
| `destination shadows a managed path` (`run.py`) | right side under `/home/aiida/.local` or `.aiida` | those hold aiida-core and profile state; pick another destination |
| `Server '…' not found in known_hosts` (`verdi computer test`) | host key not registered on the **host**, session started before `binds.conf`, or `site.yaml` missing `load_system_host_keys: true` under `ssh:` (a pre-fix copy) | redo step 2's test login; `./run.py down && ./run.py up`; add the line and rerun `tools/setup_site.py` |
| `Permission denied (publickey)` | public key not registered with the account, wrong `key_filename`, or key permissions too open | check step 2; `chmod 600 ~/.ssh/id_kudpc`; compare `site.yaml`'s `key_filename` with step 3's right-hand path |
| `verdi computer test` hangs | wrong hostname/port or a firewall; also a passphrase-protected key silently waiting for input | test plain `ssh` from the host first; see the passphrase note in step 2 |
| job dies with exit code 391, "probably out of time" in the parser message | Gaussian hit the walltime **or** memory exhaustion (an OOM kill looks identical) | check `sacct -j <slurmid> -o MaxRSS,Elapsed,State`; raise `memory_gb`/`max_wallclock_seconds` |

Still stuck? `docs/warnings.md` explains the container internals, and
`docs/plans/gaussian_plan_phase1.md` documents every design decision above.
