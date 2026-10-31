#!/usr/bin/env python3
"""Per-flow receiver. Restart every destination server when upgrading protocol."""
import argparse
import socket
import threading
import time
from flow_protocol import read_header, receive_payload, send_header

def sever_task(client, addr):
    try:
        client.settimeout(300)
        with client.makefile('rb') as stream:
            header=read_header(stream)
            size=receive_payload(stream,header['bytes'])
            received=time.monotonic()
            send_header(client,dict(flow_id=header['flow_id'],received_bytes=size,
                                    received_mono=received))
    except (OSError,ValueError,EOFError,KeyError) as error:
        print('flow receive failed:',addr,str(error),flush=True)
    finally:client.close()

def start_server(ip,port):
    with socket.socket() as server:
        server.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
        server.bind((ip,port));server.listen(128)
        print('per-flow server:',ip,port,flush=True)
        while True:
            client,addr=server.accept()
            threading.Thread(target=sever_task,args=(client,addr),daemon=True).start()

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--ip',default='0.0.0.0');p.add_argument('--port',type=int,default=5001)
    a=p.parse_args();start_server(a.ip,a.port)
