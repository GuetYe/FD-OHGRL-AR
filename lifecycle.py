"""V7 launcher-only socket checks. No changes to model or network policy."""
import socket
import errno


def check_port(port):
    with socket.socket() as probe:
        probe.settimeout(3)
        try:probe.connect(('127.0.0.1',port))
        except ConnectionRefusedError:pass
        else:raise OSError(errno.EADDRINUSE,'Live TCP listener on port '+str(port))
    # Match a reusable TCP server bind: TIME_WAIT must not impersonate a listener.
    # On Linux SO_REUSEADDR still rejects another live TCP listener.
    with socket.socket() as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(('127.0.0.1', port))
        sock.listen(1)


def finish_service(lab, run, name, wait, seconds=30):
    """Escalate only drained infrastructure, with receipt identity rechecked."""
    import os
    import signal
    from pathlib import Path
    allowed = name in ['api', 'ryu', 'broker'] or (name.startswith('h') and name[1:].isdigit())
    if not allowed:
        raise RuntimeError('Not a disposable infrastructure service: '+name)
    if lab.running(run, 'client') or lab.running(run, 'upper'):
        raise RuntimeError('Client/trainer must finish before infrastructure shutdown')
    cfg = lab.read(run/'run.json')
    if lab.pending(Path(cfg['lower']))['pending_leases'] != 0:
        raise RuntimeError('Outstanding leases: retain infrastructure for cleanup')
    lab.signal_once(run, name, 'V7 infrastructure shutdown after flow drain')
    try:
        wait(lambda:not lab.running(run, name), seconds, name+' exited')
        return
    except TimeoutError:
        pass
    info = lab.proc(run, name)
    if lab.live(info):
        expected = Path(info['cwd']).resolve()
        if Path('/proc/%s/cwd'%info['pid']).resolve() != expected:
            raise RuntimeError('Service cwd differs from receipt: '+name)
        if lab.live(info):
            print('[STOP] SIGTERM tracked '+name+' pid='+str(info['pid']), flush=True)
            try:os.kill(info['pid'], signal.SIGTERM)
            except ProcessLookupError:pass
    wait(lambda:not lab.running(run, name), seconds, name+' exited after SIGTERM')

