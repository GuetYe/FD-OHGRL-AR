"""Immutable Chen model versions for an independent fixed-bank worker."""
import json
import os
from pathlib import Path
import shutil


def atomic_json(path, data):
    path = Path(path)
    temp = path.with_suffix(path.suffix + '.tmp')
    with temp.open('w', encoding='utf-8') as stream:
        json.dump(data, stream, sort_keys=True, allow_nan=False)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(str(temp), str(path))


def capture_actor(output, agent, update, accepted_feedback):
    output = Path(output)
    snapshots = output / 'dense_actor_snapshots'
    snapshots.mkdir(exist_ok=True)
    name = 'update_%06d.pt' % update
    snapshot = snapshots / name
    if snapshot.exists():
        raise ValueError('Duplicate Chen actor version: %s' % snapshot)
    agent.save(snapshot)
    if update == 0 or accepted_feedback % 300 == 0:
        permanent = output / ('initial.pt' if update == 0 else
                              'checkpoint_%06d.pt' % accepted_feedback)
        if permanent.exists():
            raise ValueError('Duplicate permanent checkpoint: %s' % permanent)
        shutil.copy2(str(snapshot), str(permanent))
        atomic_json(str(permanent) + '.json',
                    {'update': update, 'accepted_feedback': accepted_feedback,
                     'checkpoint': permanent.name})
    row = {'update': update, 'accepted_feedback': accepted_feedback,
           'checkpoint': name}
    with (output / 'dense_actor_index.jsonl').open('a', encoding='utf-8') as stream:
        stream.write(json.dumps(row, sort_keys=True) + '\n')
        stream.flush()
        os.fsync(stream.fileno())


def finish_capture(output, status, feedback, updates):
    atomic_json(Path(output) / 'dense_actor_capture_done.json',
                {'status': status, 'accepted_feedback': feedback,
                 'updates': updates})
