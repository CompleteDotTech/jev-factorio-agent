# Read-only capacity audit and operator-owned remediation

Related: #97; integrated acceptance: #92; tracker: #103.

## Evidence boundary

`python -m jev_factorio.capacity_audit` reads fixed, bounded proc/cgroup-v2
files. It never writes a quota, changes affinity, contacts a hypervisor, stops a
process or restarts a VM. Its default target is the audit process, not an
implicitly discovered controller. Select the controller PID and the **verified
visible** hierarchy through the existing authorized operational channel.

```sh
python -m jev_factorio.capacity_audit \
  --pid "$CONTROLLER_PID" --cgroup-root "$VISIBLE_CGROUP_ROOT" \
  --leaf "$VERIFIED_CONTROLLER_LEAF" --seconds 2 > capacity.json
```

The leaf must be inside the explicitly selected root. No hierarchy scans or
process-argument dumps are performed. A relative cgroup membership that cannot
be resolved inside this view is not proof of a host-level root: resolve that
binding through the operator instead of changing the audit root to an unrelated
filesystem. Repeat separately in an authorized guest and host view when needed.
Do not equate those separately collected samples with a simultaneously aligned
cross-host experiment.

Output contains numeric counters and ordinal hierarchy levels, never cgroup
paths, host names, CPU IDs, process arguments, session identifiers or credentials.
Level 0 is the selected leaf, level 1 its parent. `cpu_max.state` distinguishes
finite, unlimited-at-that-level and unknown. The minimum visible finite quota is
an **upper bound**. Missing ancestors, enclosing VM limits, scheduler contention
or physical host headroom can make available capacity lower. `host_capacity`
therefore remains `unknown`; this tool cannot remotely attest the host.

## Interpretation and comparison

CPU quota and period are microseconds. A quota of 200000 per 100000 represents
at most two aggregate CPU equivalents, not two per advertised vCPU. Cgroup
limits are hierarchical. Keep every level's CPU accounting separate; never sum
ancestor and descendant counters. CPU `throttled_usec` is not emitted as a
percentage of elapsed wall time. CPU sets are intersected with observed process
affinity and published as counts, not topology.

The report preserves each level's sampling duration. Reads are sequential and
not atomic. Missing counters are unknown; counter decreases are resets; replaced
cgroup identities invalidate deltas. `/proc/stat` totals exclude the separately
reported guest/guest_nice fields because they are already included in user/nice.
Steal is a fraction of accounting ticks, not a predicted application speedup.
PSI describes stalled-task time; it is neither utilization nor proof that this
controller was blocked. Swap deltas are pages, with page size separately reported;
major faults are not all necessarily swap activity. Memory availability and
ancestor limits are reported separately, not conflated with reserved capacity.

Record source SHA, configuration digest, workload, initial-save binding,
comparison windows and regression limits **before** examining a treatment.
Collect application wall/process-CPU clocks, science consumption/progress,
manual coal trips and resource samples over aligned declared windows. Change
algorithm and capacity in separate controlled arms; otherwise report a confounded
trend. Do not assume removing a two-CPU quota creates a twofold speedup.

## Reproducible proposal (does not apply a change)

Obtain active and persistent settings using the existing infrastructure owner's
procedure. Preserve their exact private readback and hashes. Normalize a finite
aggregate quota to `{quota_us, period_us}`; represent explicitly verified unlimited
as `quota_us: null`, never use null for missing evidence. This pure function emits
current/proposed settings, numeric preconditions and exact rollback values:

```python
from jev_factorio.capacity_audit import allocation_plan
proposal = allocation_plan(
    current={"quota_us": 200000, "period_us": 100000},  # illustrative, not host discovery
    intended_cpus=3,
    advertised_vcpus=4,
    headroom_cpus=1.5,  # explicit operator estimate, not an attestation
)
```

The example values are a fixture, not a recommendation for any live host.
`preconditions_satisfied` covers numeric allocation/headroom only; `apply_authorized`
is always false. A proposal does not establish authority over other workloads.

For an approved libvirt/QEMU change, the owner must reconcile `global_quota` and
`global_period` (whole domain) rather than confusing them with per-vCPU quotas.
Review active and persistent configuration, ancestor cgroups, affinity and
physical headroom. Review memory pressure and workload placement at the same
time. Store the reviewed change in the canonical infrastructure repository,
link it to #97, and parameterize the domain/service selection outside this public
repository. No private machine hardcodes belong here.

Apply only through that owner's established process, bounded to the approved
workload. Read back active **and** persistent values and the actual ancestor
cgroup. If they disagree, stop acceptance. Preserve the source/treatment and
campaign cutoff. Never restart shared WSL, terminate another session, change an
unrelated workload, or raise a container quota to pretend an ancestor cap is gone.

Rollback is restoration of the exact captured active and persistent quota,
period and affinity using the same owner-controlled process, followed by readback.
It is not a world reset, a recreated campaign or a shared-host restart. Unknown
previous values block automatic rollback planning. No live change was performed
by the development fixtures for this patch.

## Primary references

- Linux cgroup v2: https://docs.kernel.org/admin-guide/cgroup-v2.html
- Linux PSI: https://docs.kernel.org/accounting/psi.html
- Linux proc accounting: https://docs.kernel.org/filesystems/proc.html
- libvirt domain CPU tuning: https://libvirt.org/formatdomain.html#cpu-tuning

## Remaining #97 acceptance

The audit/proposal code and fixtures are not a reviewed infrastructure change.
Canonical infrastructure ownership, live ancestor readback, headroom assessment,
authorized remediation and matched native before/after measurements remain open.

## Malformed-input, epoch and quota-rounding gates

Known memory fields, pressure-row fields and CPU accounting rows must be
unambiguous. Duplicate fields are rejected even when the duplicate values are
equal; the public report marks the affected measurement unavailable rather than
using the last value. Unrelated, unrecognized proc fields are still ignored.
A malformed pressure sample therefore cannot be mistaken for measured zero
pressure, and a duplicate `MemAvailable` cannot inflate the available-memory
readout. No extra runtime/game/transport calls are introduced.

Counter comparison requires the same observed, non-missing boot epoch. Cgroup
CPU accounting additionally requires the same observed directory identity and
a positive per-level local sampling interval. Missing epoch/window evidence is
`unknown`; an observed changed identity/boot is `epoch_changed`; a decreasing
counter in a qualified epoch is `reset`. None emits an invented zero delta.
The boot time and inode checks are local continuity guards, **not** host identity
attestation or a cross-host sample-binding contract. They do not prove that a
quota stayed unchanged between sequential reads. Existing schema-1 fields remain
available; older/unqualified inputs can now yield `unknown` instead of a measured
delta. Do not sum nested counters or divide a delta with no valid time window.

The audit/proposal supports CFS periods from 1,000 through 1,000,000 microseconds
and finite quotas of at least 1,000 microseconds. Invalid current settings cannot
be used to promise a valid rollback. Requests whose rounded quota is below the
supported minimum are rejected, not silently raised. Other scheduler classes,
nonstandard controllers and external host restrictions still require operator
qualification. See [CFS bandwidth control](https://docs.kernel.org/scheduler/sched-bwc.html)
and [cgroup v2 CPU](https://docs.kernel.org/admin-guide/cgroup-v2.html#cpu).

`allocation_plan` rounds the supplied decimal CPU request upward to an integer
number of quota microseconds, then computes required additional capacity from
that **actual proposed quota**. It compares exact rational quantities for the
headroom gate rather than a rounded floating-point approximation. For example,
with current quota/period `200000/100000`, a request for `2.000001` CPUs needs
quota `200001`, an increase of `0.00001` CPUs. Declared headroom `0.000002` is
insufficient, even though it exceeds the unquantized requested increase. Exact
headroom `0.00001` passes the numeric check only. The original current/rollback
values are detached copies and remain unchanged; `apply_authorized` stays false.
The additive `requested_cpu_equivalents`, `proposed_cpu_equivalents` and
`quota_rounding` fields make this distinction explicit. This is not an authorized
infrastructure change or a claim that declared host headroom was measured.

Regression command (temporary proc/cgroup fixtures, no native game):

```sh
PYTHONPATH=src python -m pytest \
  tests/test_capacity_audit.py tests/test_capacity_audit_integrity.py -q
```

These corrections do not implement or qualify the separately reported detailed
host-audit continuation, perform active/persistent libvirt readback, or finish
#97/#92 operational acceptance. Keep those source and infrastructure-owner gates
separate from this bounded correction to the merged audit API.

## Directory continuity within each sample

Each level's directory identity is read before and after its quota, accounting,
cpuset and memory files. Both reads must identify the same directory. Missing
metadata, observed removal/replacement or symlink substitution invalidates that
whole level: its identity, quotas, counters, cpuset and memory values become
unknown. Mixed values cannot contribute a capacity ceiling or a measured counter
delta. Independently validated ancestor levels remain available, while the
visible quota hierarchy is marked incomplete.

These local device/inode checks are not an atomic snapshot or a lock. They do
not detect a replacement that returns to the original identity between checks,
prove that settings remained constant, or attest a remote host. Sequential-read
and host-capacity limitations still apply. No host or game state is changed.

`tests/test_capacity_sample_identity.py` uses private filesystem fixtures to
replace, remove or substitute a symlink at every level-file read boundary; it
also covers unavailable directory metadata and unchanged-directory controls.
Run it with the two existing capacity-audit suites before publication.
