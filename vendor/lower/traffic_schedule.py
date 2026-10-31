"""Absolute monotonic arrival schedule; scheduler overhead never shifts later due times."""
import math
import time

def validate_trace_row(row, index):
    if type(row.get('sequence')) is not int or row['sequence'] != index:
        raise ValueError('traffic trace sequence mismatch at '+str(index))
    gap=row['interarrival_seconds'];size=row['size_bytes'];kind=row['flow_type']
    if isinstance(gap,bool) or not isinstance(gap,(int,float)) or not math.isfinite(gap) or gap<0:
        raise ValueError('interarrival_seconds must be finite and nonnegative')
    if type(size) is not int or not 1<=size<=1024**3 or kind not in ('T','B'):
        raise ValueError('invalid traffic trace payload/type')
    return dict(sequence=index,interarrival_seconds=float(gap),size_bytes=size,flow_type=kind)

def scheduled_arrivals(trace,start,stop,clock=time.monotonic):
    due=start
    for item in trace:
        due+=item['interarrival_seconds']
        if stop.wait(max(0,due-clock())):break
        yield item,due,clock()
