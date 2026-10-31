# FD-OHRGL-AR: Feedback-Driven Anycast Routing

This repository provides a code demonstration of feedback-driven anycast routing on an 18-node network. The method learns destination-selection probabilities and constructs a route to each candidate destination. Network feedback is collected through an SDN controller and used to update the routing policies.

## 18-node topology

The demonstration uses the `original18` topology defined in `topologies.json`.

| Property | Configuration |
|---|---|
| Network nodes | 18 |
| Undirected links | 24 |
| Source node | 3 |
| Candidate destinations | 15, 16, 17, 18 |
| Inter-switch bandwidth | 6 Mbps |
| Host-access bandwidth | 12 Mbps |
| Initial link delay | 0.1 ms |
| Initial packet loss | 0% |

The upper policy produces a distribution over the four destinations. The lower policy builds paths hop by hop. A next hop must be adjacent, unvisited, and able to reach the destination after the visited prefix is excluded. Lower-policy node features indicate the current node, destination, and visited nodes.

## Requirements

The network demonstration requires Linux, root privileges, Open vSwitch, Mininet, Mininet-WiFi, Ryu, and tmux. The controller installs routes, while TCP traffic provides application-level delivery measurements.

Use separate Python environments for model execution and network control:

- **Model environment:** Python 3.9, PyTorch 1.13.0, PyTorch Geometric, NumPy, SciPy, NetworkX, Requests, and tensorboardX. See `requirements.txt`.
- **Controller environment:** Mininet, Mininet-WiFi, Ryu, Flask, Requests, and NetworkX. See `requirements-lower.txt`.
- **System executables:** `mnexec`, `ip`, `tc`, `ovs-vsctl`, `ryu-manager`, and `tmux`.

Install the requirement files in their corresponding environments. Install Mininet and Open vSwitch separately. Select compatible PyTorch/PyG builds and preserve the resolved environment with `pip freeze`; the requirement files are not complete environment lock files.

## Configure local paths

Run the following commands from the repository root in a Bash terminal. Replace the two interpreter paths with your local environments:

```bash
export DEMO_PACKAGE="$PWD"
export MODEL_PYTHON=/absolute/path/to/model-env/bin/python
export CONTROL_PYTHON=/absolute/path/to/controller-env/bin/python
export DEMO_RUNTIME="$DEMO_PACKAGE/demo-runtime"
export DEMO_RESULTS="$DEMO_PACKAGE/demo-results"

fd_demo() {
  sudo python3 "$DEMO_PACKAGE/run_v7.py" "$@" \
    --topology original18 \
    --runtime-root "$DEMO_RUNTIME" \
    --work-root "$DEMO_RESULTS" \
    --lower-python "$CONTROL_PYTHON"
}
```

Use absolute paths without spaces. Recreate these variables and the function when opening a new terminal.

**Deployment configuration:** the current launcher retains fixed locations for its shared lock, legacy-task checks, and some network-tool search paths. Before first use on a new machine, adapt `global_lock()` and `quiet()` in `run_v7.py`, together with tool paths in `vendor/s3.py`, to the local installation. All network jobs on the same machine must share one lock. After reviewing any source edits, regenerate `SHA256.json` for the distributed files before installation. The commands below assume this local setup is complete.

## Install and check

```bash
python3 run_v7.py verify
python3 test_paper900.py

sudo python3 install_v7.py --topology original18 \
  --runtime-root "$DEMO_RUNTIME" \
  --upper-python "$MODEL_PYTHON" \
  --lower-python "$CONTROL_PYTHON"

fd_demo probe
fd_demo check
```

The installer creates a private runtime copy for the 18-node topology. `probe` checks model inference, gradient updates, and checkpoint reload. `check` validates the installed files, model startup, network tools, ports, and flow registries. Use a new runtime directory if the installed source differs from the current package.

## Run a small demonstration

```bash
fd_demo smoke --trial demo18_smoke
fd_demo status --trial demo18_smoke
sudo tail -n 80 "$DEMO_RESULTS/launcher_original18_demo18_smoke.log"
```

The smoke task runs in the background and collects 30 valid feedback samples. Wait for `SMOKE_PASSED` before proceeding. Only one network experiment should use the host at a time.

## Train and evaluate

```bash
fd_demo run --trial demo18_900 --budget 900
fd_demo status --trial demo18_900
sudo tail -n 80 "$DEMO_RESULTS/launcher_original18_demo18_900.log"
```

This 18-node workflow runs a smoke check, trains a fresh model with 900 valid feedback samples, and evaluates the final checkpoint on three traffic seeds: 5101, 5102, and 5103. Each evaluation uses 300 flows. The three seeds measure traffic variability for one trained model. The feedback budget counts accepted network feedback, rather than optimizer updates or test flows.

The launcher returns after submitting the background worker. Inspect the status and logs for completion. A full run reports `COMPLETED` after its evaluation and cleanup checks finish.

## Outputs

Results are stored under:

```text
 demo-results/original18/<trial>/
 ??? status.json
 ??? receipts.json
 ??? <run-directory>/
     ??? experiment.json
     ??? config.json
     ??? flows/
     ??? analysis_summary.json
     ??? passed.json
     ??? network_closed.json
     ??? upper/
         ??? initial.pt
         ??? latest.pt
         ??? summary.json
```

`flows/` contains per-flow delivery and cleanup records. Flow completion time is measured from scheduled arrival to delivery, including waiting before transmission. `latest.pt` is the final policy checkpoint. Inspect the summary, pass receipt, and network-closure record together when assessing a run.

## Stop and clean up

```bash
fd_demo stop --trial demo18_900
# Wait for the worker to exit and in-flight traffic to drain.
fd_demo cleanup --trial demo18_900
```

Keep failed-run records and use a new trial identifier for another attempt. Actor checkpoints do not contain complete optimizer and replay-buffer state for resuming training.

## Validation scope

Local checks cover model loading, legal-path masks, policy replacement, feedback-budget termination, and process lifecycle behavior. Full SDN operation and flow completion times require execution in the configured Linux network environment. This README describes the 18-node demonstration and does not report new measured results.
