"""Usage: python run.py --help. Does not start or reconfigure Mininet/Ryu."""
import argparse
import copy
import uuid
from collections import Counter
import json
import platform
import sys
import time
from pathlib import Path

import networkx as nx
import numpy as np
import requests
import torch
from tensorboardX import SummaryWriter

from baseline import DualAgent, Snapshot, feedback_matches, validate_config, load_profile
from dense_capture import capture_actor, finish_capture


class Journal:
    def __init__(self, directory):
        self.started=time.monotonic()
        self.directory = Path(directory)
        # Never silently mix independent trials.
        self.directory.mkdir(parents=True, exist_ok=False)
        self.file = open(self.directory / 'events.jsonl', 'a', encoding='utf-8', buffering=1)

    def write(self, kind, **data):
        self.file.write(json.dumps({'event': kind, 'wall_time': time.time(), 'elapsed_seconds':time.monotonic()-self.started, **data}, ensure_ascii=False, allow_nan=False) + '\n')

    def close(self):
        self.file.close()


class HTTPBridge:
    def __init__(self, base_url, cfg):
        self.url, self.cfg = base_url.rstrip('/'), cfg
        self.session = requests.Session()

    def get(self, endpoint):
        response = self.session.get(self.url + endpoint, timeout=self.cfg['http_timeout'])
        response.raise_for_status()
        data=response.json()
        if data.get('telemetry_semantics_version')!=self.cfg['telemetry_semantics_version']:
            raise ValueError('telemetry version mismatch; deploy matching lower-layer code')
        return Snapshot(data, self.cfg)

    def post(self, payload):
        response = self.session.post(self.url + '/api/action', json=payload, timeout=self.cfg['http_timeout'])
        response.raise_for_status()
        # Existing server echoes the JSON, but that is storage receipt, not switch ACK.
        echoed = response.json()
        for key in ['control_tag', 'high_act_list', 'low_act_dict', 'policy_version', 'published_at']:
            if echoed.get(key) != payload[key]:
                raise RuntimeError('action echo mismatch; verify server implementation')

    def initial(self):
        deadline = time.monotonic() + self.cfg['feedback_timeout']
        last_error = ''
        while time.monotonic() < deadline:
            try:
                state = self.get('/api/request')
                expected = self.cfg['normalization_profile']['topology_edge_count']
                if state.graph.number_of_edges() != expected:
                    raise ValueError('telemetry topology edge count differs from fixed profile')
                return state
            except (requests.RequestException, ValueError, KeyError) as error:
                last_error = str(error)
                time.sleep(self.cfg['poll_interval'])
        raise TimeoutError('no valid /api/request: ' + last_error)

    def feedback(self, decision, before_stamp, post_stamp, seen, candidates, progress=None):
        # Per-flow event alignment for the real asynchronous deployment:
        # 1) a target flow must consume this exact policy_version;
        # 2) that flow must select this decision's destination;
        # 3) then wait N additional distinct telemetry frames before computing
        #    reward on the intended selected path.
        # We deliberately do not require trigger-driven path markers to equal
        # the published path in the same telemetry snapshot.
        started = time.monotonic()
        time.sleep(self.cfg['control_interval'])
        settle_needed = int(self.cfg.get('feedback_settle_snapshots', 1))
        matching_stamps = set()
        counts = Counter()
        last_error = 'no target flow has consumed this policy version yet'
        last_report = started
        latest = {}
        while time.monotonic() - started < self.cfg['feedback_timeout']:
            try:
                state = self.get('/api/state')
                stamp = str(state.data['timestamp'])
                candidates.check(state)
                latest = {'timestamp': stamp,
                          'choice_policy_version': state.data.get('choice_policy_version'),
                          'choice_key': state.data.get('choice_key')}
                if stamp in seen or stamp in [before_stamp, post_stamp]:
                    reason = 'stale_or_pre_post_timestamp'
                elif not feedback_matches(state, decision, self.cfg):
                    reason = ('policy_version_mismatch' if
                              state.data.get('choice_policy_version') != decision.payload.get('policy_version')
                              else 'flow_identity_or_installation_mismatch')
                    # A break in observed alignment restarts the settling window.
                    matching_stamps.clear()
                elif stamp in matching_stamps:
                    reason = 'duplicate_matching_timestamp'
                else:
                    matching_stamps.add(stamp)
                    reason = 'settling'
                    if len(matching_stamps) > settle_needed:
                        self.last_feedback_diagnostics = {
                            'matched_frames': len(matching_stamps),
                            'wait_seconds': time.monotonic() - started,
                            'rejection_counts': dict(counts), **latest}
                        return state
                counts[reason] += 1
                last_error = reason
            except (requests.RequestException, ValueError, KeyError) as error:
                matching_stamps.clear()
                counts['invalid_or_unavailable_state'] += 1
                last_error = str(error)
            now = time.monotonic()
            if progress is not None and now - last_report >= 10:
                progress(wait_seconds=now-started, reason=last_error,
                         expected_policy_version=decision.payload.get('policy_version'),
                         matched_frames=len(matching_stamps),
                         rejection_counts=dict(counts), latest=latest)
                last_report = now
            time.sleep(self.cfg['poll_interval'])
        raise TimeoutError('no per-flow-aligned Chen feedback: ' + last_error
                           + '; rejection_counts=' + repr(dict(counts))
                           + '; latest=' + repr(latest))


def parser():
    p = argparse.ArgumentParser(description='Chen-Dual-Packet, no TL; legacy HTTP adapter')
    p.add_argument('mode', choices=['decide', 'train-http', 'eval-http'])
    p.add_argument('--config', default=str(Path(__file__).with_name('config.json')))
    p.add_argument('--snapshot', help='existing /api/request JSON for read-only decide')
    p.add_argument('--url', help='existing API server, e.g. http://10.33.75.172:5000')
    p.add_argument('--checkpoint', help='own checkpoint for evaluation or resuming a new training run')
    p.add_argument('--output', default='runs/chen_trial')
    p.add_argument('--updates', type=int, default=100, help='additional FLX updates; each uses 2N-1 control intervals')
    p.add_argument('--steps', type=int, default=100, help='evaluation control intervals')
    p.add_argument('--seed', type=int)
    p.add_argument('--stochastic-eval', action='store_true', help='sample actions instead of argmax (recorded in manifest)')
    p.add_argument('--destination-mode', choices=['distribution','legacy'], default='distribution',
                   help='eval only: raw destination probabilities per flow (default), or legacy one-hot')
    p.add_argument('--feedback-timeout', type=float, help='override checkpoint runtime timeout')
    p.add_argument('--control-interval', type=float, help='override checkpoint minimum wait')
    p.add_argument('--poll-interval', type=float, help='override checkpoint polling interval')
    p.add_argument('--feedback-settle-snapshots', type=int, help='override additional matching frames')
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    if args.mode!='train-http' and args.destination_mode=='distribution' and args.stochastic_eval:
        raise ValueError('distribution evaluation keeps deterministic lower paths; do not combine --stochastic-eval')
    cfg = json.loads(Path(args.config).read_text(encoding='utf-8'))
    if args.checkpoint:
        saved = torch.load(args.checkpoint, map_location='cpu', weights_only=False)
        if saved.get('format') != 2:
            raise ValueError('old reward checkpoint is incompatible; retrain')
        cfg = saved['config']
    if args.seed is not None and not args.checkpoint:
        cfg['seed'] = args.seed
    cfg = copy.deepcopy(cfg)
    for name in ['feedback_timeout', 'control_interval', 'poll_interval', 'feedback_settle_snapshots']:
        value = getattr(args, name)
        if value is not None:
            cfg[name] = value
    validate_config(cfg)
    if cfg['normalization_profile'] != load_profile():
        raise ValueError('checkpoint profile differs from shared network_profile.json; use matching experiment or retrain')
    if cfg.get('feedback_alignment') != 'per_flow_installation':
        raise ValueError('this canonical Chen package requires feedback_alignment=per_flow_installation')
    if not isinstance(cfg.get('feedback_settle_snapshots'), int) or cfg['feedback_settle_snapshots'] < 0:
        raise ValueError('feedback_settle_snapshots must be a nonnegative integer')
    if args.updates < 1 or args.steps < 1:
        raise ValueError('updates/steps must be positive')
    if args.mode == 'eval-http' and not args.checkpoint:
        raise ValueError('eval-http requires a trained checkpoint')
    bridge = None
    if args.mode == 'decide':
        if not args.snapshot:
            raise ValueError('decide requires --snapshot')
        state = Snapshot(json.loads(Path(args.snapshot).read_text(encoding='utf-8')), cfg)
    else:
        if not args.url:
            raise ValueError('HTTP mode requires --url')
        bridge = HTTPBridge(args.url, cfg)
        state = bridge.initial()
    agent = DualAgent.load(args.checkpoint, state) if args.checkpoint else DualAgent(cfg, state)
    agent.cfg.update({k: cfg[k] for k in ['feedback_timeout', 'control_interval', 'poll_interval', 'feedback_settle_snapshots']})
    if args.seed is not None:
        torch.manual_seed(args.seed)
        np.random.seed(args.seed)
    if args.mode == 'decide':
        decision = agent.decide(state, deterministic=True, tag='C',
                                destination_mode='distribution' if args.destination_mode=='distribution' else 'sample')
        print(json.dumps({'warning': 'untrained model, interface inspection only' if not args.checkpoint else 'frozen model inference',
                          'action': decision.payload, 'destination': decision.destination,
                          'proposal_order': decision.proposal_order}, indent=2))
        return 0
    journal = Journal(args.output)
    tensorboard_dir = journal.directory / 'tensorboard'
    tensorboard_writer = SummaryWriter(logdir=str(tensorboard_dir), flush_secs=10)
    start_updates = agent.updates
    run_token = uuid.uuid4().hex
    manifest = {'method': 'Chen-Dual-Packet (no TL)', 'config': cfg, 'arguments': vars(args),
                'python': platform.python_version(), 'torch': torch.__version__, 'numpy': np.__version__,
                'networkx': nx.__version__, 'reward': 'post-action telemetry path proxy, not flow E2E delay',
                'training': 'single learner; finite-window 2N-1/first-N actor-critic; discard look-ahead tail after update',
                'evaluation_deterministic': args.destination_mode=='legacy' and not args.stochastic_eval,
                'evaluation_destination_mode': args.destination_mode,
                'checkpoint_updates_at_start': start_updates,
                'run_token': run_token,
                'feedback_alignment': cfg['feedback_alignment'],
                'tensorboard_dir': str(tensorboard_dir),
                'candidates': agent.candidates.paths}
    (journal.directory / 'manifest.json').write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding='utf-8')
    if args.mode == 'train-http':
        capture_actor(journal.directory, agent, 0, 0)
    print(json.dumps({'event': 'start', 'output': str(journal.directory.resolve()),
                      'tensorboard': str(tensorboard_dir.resolve()),
                      'feedback_timeout': cfg['feedback_timeout'],
                      'samples_per_update': 2*cfg['horizon']-1,
                      'checkpoint_updates_at_start': start_updates}), flush=True)
    seen = {str(state.data['timestamp'])}
    step = 0
    status = 0
    try:
        while ((agent.updates - start_updates < args.updates) if args.mode == 'train-http' else (step < args.steps)):
            train = args.mode == 'train-http'
            start = time.monotonic()
            decision = agent.decide(state, deterministic=(not train and not args.stochastic_eval), tag='T' if train else 'C',
                                    destination_mode='distribution' if not train and args.destination_mode=='distribution' else 'sample')
            # Version every publication with a run-unique token so stale
            # telemetry from a previous process cannot satisfy per-flow alignment.
            decision.payload['policy_version'] = f'CHEN-{run_token}-{step + 1:08d}'
            decision.payload['published_at'] = time.time()
            inference_s = time.monotonic() - start
            # Read current feedback timestamp before POST; endpoints need not update together.
            before = str(bridge.get('/api/state').data['timestamp'])
            journal.write('publish_attempt', step=step, action=decision.payload,
                          master_probabilities=decision.master_probabilities, proposal_order=decision.proposal_order,
                          destination_mode=decision.destination_mode)
            bridge.post(decision.payload)
            print(json.dumps({'event': 'action', 'step': step,
                              'policy_version': decision.payload['policy_version'],
                              'destination': decision.destination, 'path': decision.path}), flush=True)
            journal.write('action', step=step, observation=state.data, action=decision.payload,
                          destination=decision.destination, proposal_order=decision.proposal_order,
                          master_probabilities=decision.master_probabilities,
                          inference_seconds=inference_s, model_update=agent.updates)
            post = str(bridge.get('/api/state').data['timestamp'])
            def report_wait(**details):
                journal.write('waiting_feedback', step=step, **details)
                print(json.dumps({'event': 'waiting_feedback', 'step': step, **details}), flush=True)
            next_state = bridge.feedback(decision, before, post, seen, agent.candidates, progress=report_wait)
            if not train and args.destination_mode=='distribution':
                # Identity/path were verified against the immutable published distribution.
                decision.path = list(next_state.data['applied_path'])
                decision.destination = decision.path[-1]
            key = str((cfg['source'], decision.destination))
            intended = {frozenset((u,v)) for u,v in zip(decision.path, decision.path[1:])}
            markers_valid = all(e.get(key) in (0,1) for e in next_state.data['edges'])
            marked = {frozenset((e['source'],e['target'])) for e in next_state.data['edges'] if e.get(key) == 1}
            path_marker_match = (intended == marked) if markers_valid else None
            reward = next_state.reward(decision.path)
            seen.add(str(next_state.data['timestamp']))
            print(json.dumps({'event': 'feedback', 'step': step, 'reward_proxy': reward,
                              'path_marker_match': path_marker_match,
                              'window_samples': len(agent.buffer)+1 if train else 0}), flush=True)
            decision_interval_s = time.monotonic() - start
            performance = next_state.performance(decision.path)
            raw_metrics = performance['metrics']
            normalized_metrics = performance['normalized']
            journal.write('feedback', step=step, reward_proxy=reward, observation=next_state.data,
                          choice_key=key, path=decision.path, destination=decision.destination,
                          path_marker_match=path_marker_match,
                          alignment_diagnostics=bridge.last_feedback_diagnostics,
                          decision_interval_seconds=decision_interval_s,
                          path_metrics=list(raw_metrics),
                          normalized_metrics=normalized_metrics,
                          saturated=performance['saturated'],
                          reference_exceeded=performance['reference_exceeded'],
                          normalization_edge_count=cfg['normalization_profile']['topology_edge_count'])

            # TensorBoard: interaction / routing metrics use control step as x-axis.
            tensorboard_writer.add_scalar('Train/Reward/path_reward_proxy', float(reward), step)
            tensorboard_writer.add_scalar('Train/Efficiency/inference_seconds', float(inference_s), step)
            tensorboard_writer.add_scalar('Train/Efficiency/decision_interval_seconds', float(decision_interval_s), step)
            tensorboard_writer.add_scalar('Train/Path/hops', int(performance['hops']), step)
            raw_names = ['bottleneck_free_bw_mbps', 'delay_ms', 'loss_fraction', 'queue_packets']
            norm_names = ['bottleneck_free_bw', 'delay', 'loss', 'queue']
            for name, value in zip(raw_names, raw_metrics):
                tensorboard_writer.add_scalar('Train/PathRaw/' + name, float(value), step)
            for name, value in zip(norm_names, normalized_metrics):
                tensorboard_writer.add_scalar('Train/PathNormalized/' + name, float(value), step)
            diag = getattr(bridge, 'last_feedback_diagnostics', {}) or {}
            if 'wait_seconds' in diag:
                tensorboard_writer.add_scalar('Train/Feedback/wait_seconds', float(diag['wait_seconds']), step)
            if 'matched_frames' in diag:
                tensorboard_writer.add_scalar('Train/Feedback/matched_frames', int(diag['matched_frames']), step)
            for reason, count in (diag.get('rejection_counts') or {}).items():
                tensorboard_writer.add_scalar('Train/Feedback/rejections/' + str(reason), int(count), step)
            if path_marker_match is not None:
                tensorboard_writer.add_scalar('Train/Feedback/path_marker_match', float(bool(path_marker_match)), step)

            if train:
                result = agent.observe(decision, reward)
                if result:
                    journal.write('update', **result)
                    update_index = int(result['update'])
                    tensorboard_writer.add_scalar('Training/return_mean', float(result['return_mean']), update_index)
                    tensorboard_writer.add_scalar('Training/lower_loss', float(result['lower_loss']), update_index)
                    tensorboard_writer.add_scalar('Training/master_loss', float(result['master_loss']), update_index)
                    tensorboard_writer.add_scalar('Training/lower_grad_norm', float(result['lower_grad_norm']), update_index)
                    tensorboard_writer.add_scalar('Training/master_grad_norm', float(result['master_grad_norm']), update_index)
                    tensorboard_writer.add_scalar('Training/collected', int(result['collected']), update_index)
                    tensorboard_writer.add_scalar('Training/loss_samples_per_agent', int(result['loss_samples_per_agent']), update_index)
                    tensorboard_writer.add_scalar('Training/learning_rate', float(cfg['learning_rate']), update_index)
                    tensorboard_writer.flush()
                    agent.save(journal.directory / 'checkpoint.pt')
                    capture_actor(journal.directory, agent, update_index, step + 1)
                    print(json.dumps(result), flush=True)
            else:
                print(json.dumps({'step': step, 'reward_proxy': reward, 'destination': decision.destination}), flush=True)
            state, step = next_state, step + 1
    except KeyboardInterrupt:
        journal.write('interrupted', completed_steps=step)
        status = 130
    except Exception as error:
        journal.write('failed', completed_steps=step, error=repr(error))
        print(str(error), file=sys.stderr)
        status = 1
    finally:
        if args.mode == 'train-http':
            agent.save(journal.directory / 'checkpoint.pt')
            finish_capture(journal.directory, status, step, agent.updates)
        journal.write('end', status=status, completed_steps=step, updates=agent.updates,
                      unused_window_samples=len(agent.buffer),
                      note='Last published action remains on server. No automatic network cleanup.')
        tensorboard_writer.flush()
        tensorboard_writer.close()
        journal.close()
        bridge.session.close()
    return status


if __name__ == '__main__':
    sys.exit(main())
