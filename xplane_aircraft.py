"""Read aircraft identity via RREF byte arrays; never infer identity from flight data.

String protocol: https://xppython3.readthedocs.io/en/latest/development/udp/rref.html
Refs/lengths checked against the installed X-Plane 11 Resources/plugins/DataRefs.txt.
"""
import argparse
import math
import re
import socket
import struct
import time

FIELDS = [('icao','sim/aircraft/view/acf_ICAO',40),
          ('author','sim/aircraft/view/acf_author',128),
          ('description','sim/aircraft/view/acf_descrip',128)]
REQUESTS = [(10000+i, field, offset, f'{ref}[{offset}]')
            for i,(field,offset,ref) in enumerate((field,j,ref) for field,ref,n in FIELDS for j in range(n))]
LOOKUP = {index:(field,offset) for index,field,offset,ref in REQUESTS}


def packets(hz=1):
    return [b'RREF\0'+struct.pack('<ii400s',hz,index,ref.encode()) for index,_,_,ref in REQUESTS]


def subscribe(sock,target,hz=1):
    # Spread requests across simulator frames; burst loss can leave one missing
    # character and prevent aircraft identification indefinitely on a busy sim.
    for packet in packets(hz):
        sock.sendto(packet,target)
        time.sleep(.003)


def identity_key(identity):
    return tuple(identity.get(field,'') for field,_,_ in FIELDS)


def aircraft_mode(identity):
    if not identity or not (identity.get('icao') or identity.get('description')):return None
    text=' '.join(identity.get(k,'') for k in ['author','description']).casefold()
    compact=re.sub(r'[^a-z0-9]','',text)
    # The installed ToLiss A319 identifies its author as "Gliding Kiwi", not ToLiss.
    is_airbus=bool(re.fullmatch(r'A3\d\d',identity.get('icao','').upper())) or 'airbus' in text
    if 'toliss' in compact or ('glidingkiwi' in compact and is_airbus):return 'landing'
    return 'timed'


class AircraftIdentity:
    def __init__(self):
        self.values={};self.seen=set();self.candidate=None;self.confirmed=None
        self.last_complete=None;self.changing=True
    def observe(self,packet,now):
        if packet[:4]!=b'RREF' or len(packet)<5 or (len(packet)-5)%8:return
        for index,value in struct.iter_unpack('<if',packet[5:]):
            if index in LOOKUP and math.isfinite(value) and value.is_integer() and 0<=value<=255:
                self.values[index]=int(value);self.seen.add(index)
        if len(self.seen)!=len(REQUESTS):return
        identity={field:bytes(self.values[index] for index,f,_,_ in REQUESTS if f==field).split(b'\0',1)[0].decode('utf-8','replace').strip()
                  for field,_,_ in FIELDS}
        self.seen.clear();self.last_complete=now
        key=identity_key(identity)
        if aircraft_mode(identity) is None:
            self.candidate=None;self.confirmed=None;self.changing=True
        elif key==self.candidate:
            self.confirmed=identity;self.changing=False
        else:
            self.candidate=key;self.changing=True
    def snapshot(self,now):
        if self.changing or self.last_complete is None or now-self.last_complete>4:return None
        return dict(self.confirmed) if self.confirmed else None


def probe(host='127.0.0.1',port=49000,seconds=8):
    target=(socket.gethostbyname(host),port);sock=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
    sock.bind(('0.0.0.0',0));sock.settimeout(.25);identity=AircraftIdentity();end=time.monotonic()+seconds
    try:
        subscribe(sock,target)
        while time.monotonic()<end:
            try:p,source=sock.recvfrom(65535)
            except socket.timeout:continue
            if source[0]!=target[0]:continue
            identity.observe(p,time.monotonic());result=identity.snapshot(time.monotonic())
            if result:return result
        return None
    finally:
        subscribe(sock,target,0)
        sock.close()

if __name__=='__main__':
    import json
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--host',default='127.0.0.1');ap.add_argument('--port',type=int,default=49000)
    args=ap.parse_args();result=probe(args.host,args.port)
    print(json.dumps({'aircraft':result,'mode':aircraft_mode(result)}))
