"""One socket, immutable action/path snapshot, and DSCP lease per flow."""
import json
import math
import random
import socket
import time
import uuid
from pathlib import Path
from urllib.request import urlopen
from flow_registry import Registry, DEFAULT_DB
from flow_protocol import read_header, send_header
from lower_tensorboard import log_flow_result

def choose_action(servers, source, api_url, rng, controlled=True, fixed_destination=None,
                  policy_socket=None, policy_wait_timeout=300, stop=None, policy_prefix=None):
    if controlled:
        from policy_access import wait_policy
        action, wait_seconds = wait_policy(api_url, servers, source, socket_path=policy_socket,
                                           wait_timeout=policy_wait_timeout, stop=stop, expected_prefix=policy_prefix)
        probs=action['high_act_list'][0]
        key=rng.choices(list(servers),weights=probs,k=1)[0]
        path=action['low_act_dict'][key]
        version=action.get('policy_version') or action.get('policy_id')
        return servers[key],dict(choice_key=key,path=list(path),action=action,
                                policy_version=version,route_mode='policy',
                                policy_wait_seconds=wait_seconds)
    key=fixed_destination or rng.choice(list(servers))
    return servers[key],dict(choice_key=key,path=None,action={},policy_version=None,
                            route_mode='shortest',policy_wait_seconds=0)

def write_record(directory, record):
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    target=directory/(record['flow_id']+'.json');tmp=target.with_suffix('.tmp')
    tmp.write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8');tmp.replace(target)
    # Log only after the durable JSON record is committed. TensorBoard is auxiliary.
    log_flow_result(record)

def run_flow(source_ip,server,decision,size,flow_type,arrival_mono,output,
             database=DEFAULT_DB,setup_timeout=30,transfer_timeout=300,cleanup_timeout=60):
    reg=Registry(database)
    sock=socket.socket(socket.AF_INET,socket.SOCK_STREAM)
    lease=None
    result=dict(decision,flow_id=uuid.uuid4().hex,flow_type=flow_type,bytes=size,arrival_mono=arrival_mono,
                policy_wait_seconds=decision.get('policy_wait_seconds',0),
                task_queue_seconds=decision.get('task_queue_seconds',0))
    try:
        sock.settimeout(transfer_timeout)
        # Binding first fixes the TCP tuple before switches are programmed.
        sock.bind((source_ip,0));src,src_port=sock.getsockname()
        lease=reg.register(dict(decision,src_ip=src,dst_ip=server[0],src_port=src_port,
                                dst_port=server[1],ip_proto=6,bytes=size,flow_type=flow_type,
                                arrival_mono=arrival_mono))
        result['flow_id']=lease['flow_id']
        sock.setsockopt(socket.IPPROTO_IP,socket.IP_TOS,lease['tos'])
        sock.setsockopt(socket.SOL_SOCKET,socket.SO_PRIORITY,0)  # identifier must not grant QoS priority
        deadline=time.monotonic()+setup_timeout
        while time.monotonic()<deadline:
            current=reg.get(lease['flow_id'])
            if current['state']=='ready':break
            if current.get('controller_error'):raise RuntimeError(current['controller_error'])
            time.sleep(.05)
        else:raise TimeoutError('flow installation not confirmed by controller')
        started=time.monotonic()
        active=reg.update(lease['flow_id'],state='active',allowed=('ready',),
                          started_at=time.time(),started_mono=started)
        if active is None:raise RuntimeError('lease no longer ready')
        sock.connect(server)
        send_started=time.monotonic()
        send_header(sock,{'flow_id':lease['flow_id'],'bytes':size})
        left=size;chunk=b'x'*65536
        while left:
            n=min(left,len(chunk));sock.sendall(chunk[:n]);left-=n
        with sock.makefile('rb') as stream:ack=read_header(stream)
        ack_time=time.monotonic()
        if ack.get('flow_id')!=lease['flow_id'] or ack.get('received_bytes')!=size:raise ValueError('invalid completion ACK')
        received=float(ack['received_mono'])
        if not math.isfinite(received) or not send_started<=received<=ack_time:raise ValueError('receiver monotonic clock is not shared; use ACK FCT only')
        result.update(app_outcome='completed',app_fct_seconds=ack_time-started,
                      delivery_seconds=received-started,payload_delivery_seconds=received-send_started,
                      queue_and_install_seconds=started-arrival_mono,arrival_to_delivery_seconds=received-arrival_mono,
                      completed_mono=ack_time)
    except Exception as error:
        result.update(app_outcome='failed',error=repr(error))
    finally:
        sock.close()
        if lease:
            reg.client_closed(lease['flow_id'],**{k:v for k,v in result.items() if k!='flow_id'})
            deadline=time.monotonic()+cleanup_timeout
            while time.monotonic()<deadline:
                current=reg.get(lease['flow_id'])
                if current['state']=='released':break
                time.sleep(.1)
            current=reg.get(lease['flow_id']);result=dict(current,**result)
            result['cleanup_confirmed']=current['state']=='released'
            # Timeout here only reports a held lease; it NEVER returns DSCP to pool.
        else:result['cleanup_confirmed']=False
        write_record(output,result)
    return result
