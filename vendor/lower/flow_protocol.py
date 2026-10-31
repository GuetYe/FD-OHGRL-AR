"""Length-framed benchmark protocol. One application flow per TCP connection."""
import json

def send_header(sock, obj):
    sock.sendall(json.dumps(obj,separators=(',',':')).encode('utf-8')+b'\n')

def read_header(stream):
    line=stream.readline(4097)
    if not line.endswith(b'\n') or len(line)>4096:raise ValueError('invalid/oversize protocol header')
    value=json.loads(line)
    if not isinstance(value,dict):raise ValueError('JSON object required')
    return value

def receive_payload(stream, size):
    if not isinstance(size,int) or not 0<=size<=1024**3:raise ValueError('invalid payload size')
    left=size
    while left:
        chunk=stream.read(min(left,65536))
        if not chunk:raise EOFError('peer disconnected before complete payload')
        left-=len(chunk)
    return size
