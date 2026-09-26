"""Operator console and timed X-Plane sessions; cloud work never blocks the timer."""
from __future__ import annotations
import argparse
import curses
import json
import queue
import socket
import struct
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from xplane_live import (Flight, Receiver, SIGNALS, FLIGHT_METADATA, CHALLENGE_PREFIX,
                         LandingCut, AltitudeReset, next_remote_number, reserve_challenge_name)
from xplane_aircraft import aircraft_mode, identity_key

ACROBATIC_PREFIX = "Marple_Acrobatic_"


def alert_packet(message):
    lines=message.splitlines()
    if len(lines)>4 or any(len(s.encode('utf-8'))>239 for s in lines):
        raise ValueError('ALRT accepts four lines of at most 239 UTF-8 bytes')
    return b'ALRT\0'+struct.pack('<240s240s240s240s',*[s.encode() for s in (lines+['']*4)[:4]])


def command_packet(command):
    return b'CMND\0'+command.encode('ascii')+b'\0'


class SessionTimer:
    """Monotonic wall-clock deadline, independent of uploads and simulation speed."""
    def __init__(self,duration=60):
        self.duration=duration;self.started=None;self.warned=False;self.ended=False
    def start(self,now):self.started=now
    def tick(self,now):
        if self.started is None or self.ended:return []
        elapsed=now-self.started
        if elapsed>=self.duration:self.ended=True;return ['stop']
        if elapsed>=self.duration-10 and not self.warned:
            self.warned=True;return ['warning']
        return []
    def remaining(self,now):return max(0,self.duration-(now-self.started)) if self.started is not None else self.duration


class UploadWorker(threading.Thread):
    def __init__(self,folder,number,name,stream,interval,log,metadata=None):
        super().__init__(daemon=True)
        self.jobs=queue.Queue(maxsize=20000);self.folder=folder;self.number=number
        self.name=name;self.stream=stream;self.interval=interval;self.log=log;self.metadata=metadata
        self.ready=threading.Event();self.done=threading.Event();self.error=None;self.flight=None
        self.finish_reason=None
    def run(self):
        try:
            self.flight=Flight(self.folder,self.number,self.stream,self.name,log=self.log,metadata=self.metadata)
            self.ready.set();last=time.monotonic()
            while True:
                try:job=self.jobs.get(timeout=.05)
                except queue.Empty:job=None
                if job is not None:
                    timestamp,values=job;self.flight.add(timestamp,values)
                if self.finish_reason is not None and self.jobs.empty():
                    self.flight.finish(self.finish_reason);break
                if time.monotonic()-last>=self.interval:
                    self.flight.flush();last=time.monotonic()
        except BaseException as exc:
            self.error=type(exc).__name__;self.log('Cloud worker failed: '+self.error)
            if self.flight and not self.flight.file.closed:self.flight.file.close()
        finally:self.ready.set();self.done.set()
    def finish(self,reason):self.finish_reason=reason


class SessionController:
    def __init__(self,receiver,folder,stream=None,interval=1,duration=60,delay=5,auto_route=False):
        self.receiver=receiver;self.folder=folder;self.stream=stream;self.interval=interval
        self.auto_route=auto_route;self.mode=None if auto_route else 'timed';self.aircraft=None
        self.requested_start=False;self.landing_cycle=False;self.reset_pending=False
        self.landing=LandingCut();self.reset_watch=AltitudeReset();self.minimum_numbers={}
        self.sequence_path=Path('outputs/xplane/challenge-sequence.json')
        self.duration=duration;self.delay=delay;self.state='READY';self.message='Press S to start recording & pushing data.'
        self.latest={};self.seen=set();self.last_packet=None;self.worker=None;self.number=0
        self.countdown_until=None;self.timer=SessionTimer(duration);self.events=[]
        self.pause_sent=None;self.pause_confirmed=False;self.exit_requested=False
        self.record_file=None;self.record_path=None;self.session_manifest={};self.commands=queue.Queue();self.closed=False
        self.log_file=(folder/'session.log').open('a',buffering=1)
    def route(self,now):
        if not self.auto_route:return
        identity=self.receiver.identity.snapshot(now)
        if identity is None:
            if self.state in {'RECORDING','COUNTDOWN','WAITING'}:
                self.stop(now,'aircraft identity unavailable',pause=False)
            self.aircraft=None
            if self.state not in {'SAVING','ERROR'}:
                self.state='WAIT_ID';self.message='Waiting for confirmed aircraft identity over UDP…'
            return
        if self.aircraft is not None and identity_key(identity)==identity_key(self.aircraft):return
        if self.state in {'RECORDING','COUNTDOWN','WAITING'}:
            self.stop(now,'aircraft changed',pause=False)
        if self.state=='SAVING':return  # Finish the old dataset before applying new metadata.
        self.aircraft=dict(identity);self.mode=aircraft_mode(identity)
        self.landing=LandingCut();self.reset_watch=AltitudeReset();self.reset_pending=False
        self.state='READY';self.landing_cycle=False
        label=identity.get('description') or identity.get('icao')
        self.log(f'Aircraft: {label}; selected {self.mode} flow.')
        self.message='Press S for a 60-second Marple Acrobatic session.'
        if self.mode=='landing' or self.requested_start:self.start(now)

    def begin_recording(self,now):
        self.timer.start(now);self.state='RECORDING'
        self.message='Recording landing; waiting for first gear compression.' if self.mode=='landing' else 'START — fly for 60 seconds.'
        self.record_path=self.folder/f'session-{self.number:03d}.jsonl'
        self.record_file=self.record_path.open('x',buffering=1)
        self.session_manifest={'state':'CAPTURING','name':self.worker.name,
            'dataset_id':self.worker.flight.manifest.get('dataset_id'),
            'mode':self.mode,'aircraft':self.aircraft,
            'timer':'15 seconds after first gear compression' if self.mode=='landing' else '60 wall-clock seconds',
            'start_monotonic':now}
        if self.mode!='landing':self.session_manifest['cutoff_monotonic']=now+self.duration
        self.save_session()
        if self.mode!='landing':self.send_alert('START\nYou have 60 seconds.\nDismiss this message and fly.')

    def log(self,message,**kw):
        self.events.append(str(message));self.events=self.events[-6:]
        self.log_file.write(datetime.now(timezone.utc).isoformat()+' '+str(message)+'\n')
    def save_session(self):
        if self.record_path:
            self.record_path.with_suffix('.json').write_text(json.dumps(self.session_manifest,indent=2)+'\n')
    def connected(self,now):return self.last_packet is not None and now-self.last_packet<2
    def fresh(self,name,now):
        value,at=self.latest.get(name,(None,-1e30));return value if now-at<1 else None
    def send_alert(self,message):
        self.receiver.sock.sendto(alert_packet(message),self.receiver.target);self.log('X-Plane alert: '+message.replace('\n',' / '))
    def start(self,now):
        if self.state not in {'READY','COMPLETE','ERROR','WAIT_ID','WAIT_RESET'}:return
        if self.worker and not self.worker.done.is_set():return
        if self.auto_route and self.aircraft is None:
            self.requested_start=True;self.state='WAIT_ID';return
        self.requested_start=False
        if self.mode=='landing':
            self.landing_cycle=True;self.landing=LandingCut();self.reset_watch=AltitudeReset();self.reset_pending=False
        self.record_path=None;self.session_manifest={}
        self.worker=None;self.state='WAITING';self.message='Waiting for live, unpaused X-Plane telemetry…'
        self.countdown_until=None;self.timer=SessionTimer(self.duration);self.pause_sent=None;self.pause_confirmed=False
    def prepare(self,now):
        self.number+=1
        namespace=f'stream:{self.stream.id}' if self.stream else 'local'
        # Remote maximum was read at connection setup; reservations persist across restarts.
        prefix=CHALLENGE_PREFIX if self.mode=='landing' else ACROBATIC_PREFIX
        minimum=self.minimum_numbers.get(prefix,getattr(self,'minimum_number',1) if self.mode=='landing' else 1)
        if self.mode!='landing':namespace+=':acrobatic'
        name=reserve_challenge_name(self.sequence_path,namespace,minimum,prefix=prefix)
        metadata={**FLIGHT_METADATA,'A/C Model':'Airbus A320' if self.mode=='landing' else 'Marple Acrobatic'}
        if self.aircraft:
            metadata.update({'X-Plane Aircraft':self.aircraft.get('description',''),
                             'X-Plane ICAO':self.aircraft.get('icao',''),'Session Mode':self.mode})
        self.worker=UploadWorker(self.folder,self.number,name,self.stream,self.interval,self.log,metadata=metadata)
        self.worker.start();self.countdown_until=now+(0 if self.mode=='landing' else self.delay);self.state='COUNTDOWN'
        self.message='Get ready — recording starts after the countdown.'
    def stop(self,now,reason='operator stop',pause=False):
        if reason=='operator stop':
            self.landing_cycle=False;self.requested_start=False;self.reset_pending=False
        if self.mode=='landing':pause=False
        if self.state in {'WAIT_ID','WAIT_RESET'}:
            self.state='READY';self.message='Stopped. Press S to start again.'
        if self.state=='RECORDING':
            self.state='SAVING';self.message='Recording stopped. Uploading remaining data and finalizing Marple…'
            if self.record_file:self.record_file.close();self.record_file=None
            self.session_manifest.update(state='LOCAL_CAPTURE_COMPLETE',end_reason=reason,capture_complete=True)
            self.save_session()
            if pause:
                paused=self.fresh('paused',now)
                if paused==0:
                    self.receiver.sock.sendto(command_packet('sim/operation/pause_toggle'),self.receiver.target)
                    self.pause_sent=now;self.log('Pause requested; awaiting telemetry confirmation.')
                elif paused==1:self.pause_confirmed=True
                else:self.log('Pause not sent: no fresh pause state. Pause X-Plane manually.')
            self.worker.finish(reason)
        elif self.state in {'WAITING','COUNTDOWN'}:
            if self.worker:
                self.worker.finish('cancelled before recording');self.state='SAVING'
            else:self.state='READY'
            self.message='Start cancelled.'
    def feed(self,timestamp,mono,values):
        self.route(mono)
        self.last_packet=mono;self.seen.update(values)
        self.latest.update({k:(v,mono) for k,v in values.items()})
        if self.mode=='landing' and self.aircraft is not None:
            if self.reset_watch.observe(values,mono):
                self.reset_pending=True
                if self.state=='RECORDING':self.stop(mono,'reset before landing cutoff')
            if self.state=='RECORDING':
                previous=self.landing.touchdown;self.landing.observe(mono,values)
                if previous is None and self.landing.touchdown is not None:
                    self.reset_watch.landed=True
                    self.session_manifest.update(touchdown_time_ns=timestamp,cutoff_time_ns=timestamp+15_000_000_000)
                    self.save_session();self.log('First gear compression: recording 15 more seconds.')
                if self.landing.expired(mono):self.stop(mono,'15 seconds after first gear compression')
        in_window=(self.mode=='landing' or (self.timer.started is not None and mono<self.timer.started+self.duration))
        if self.state=='RECORDING' and self.timer.started<=mono and in_window:
            # Separate journal remains authoritative even if the cloud worker fails.
            self.record_file.write(json.dumps({'time':timestamp,**values})+'\n')
            if not self.worker.done.is_set():
                try:self.worker.jobs.put_nowait((timestamp,values))
                except queue.Full:
                    self.log('Upload queue full; ending session. Complete controller journal retained.')
                    self.stop(time.monotonic(),'upload queue full',pause=True)
        if self.pause_sent is not None and mono>=self.pause_sent and values.get('paused')==1:
            if not self.pause_confirmed:self.log('X-Plane pause confirmed.')
            self.pause_confirmed=True
            self.session_manifest['pause_confirmed']=True;self.save_session()
    def tick(self,now):
        self.route(now)
        while not self.commands.empty():
            key=self.commands.get_nowait()
            if key=='start':self.start(now)
            elif key=='stop':self.stop(now)
            elif key=='quit':self.exit_requested=True;self.stop(now)
        if self.receiver.failure:
            self.log('UDP receiver failed: '+self.receiver.failure);self.stop(now,'UDP failure',pause=False)
            if self.state not in {'SAVING','RECORDING'}:self.state='ERROR';self.message='UDP receiver failed. Restart the service.'
        if self.state=='WAITING' and (not self.auto_route or self.aircraft is not None) and self.connected(now) and self.fresh('paused',now)==0 and self.fresh('replay',now)==0:self.prepare(now)
        if self.state=='COUNTDOWN':
            if not self.connected(now) or self.fresh('paused',now)!=0 or self.fresh('replay',now)!=0:
                self.countdown_until=now+(0 if self.mode=='landing' else self.delay);self.message='Waiting for unpaused flight; countdown will restart.'
            elif now>=self.countdown_until and self.worker.ready.is_set():
                if self.worker.error:self.state='ERROR';self.message='Unable to prepare recording; see session.log.'
                else:
                    self.begin_recording(now)
        if self.state=='RECORDING':
            if self.mode=='landing':
                if self.landing.expired(now):self.stop(now,'15 seconds after first gear compression')
            else:
                for event in self.timer.tick(now):
                    if event=='warning':self.send_alert('10 seconds to go')
                    elif event=='stop':self.stop(now,'60-second session complete',pause=True)
        if self.state=='SAVING' and self.worker and self.worker.done.is_set():
            manifest=self.worker.flight.manifest if self.worker.flight else {}
            success=not self.worker.error and manifest.get('state') in {'FINISHED','LOCAL_CAPTURE_COMPLETE'} and not manifest.get('upload_status')
            self.state='COMPLETE' if success else 'ERROR'
            self.message='Saved and verified in Marple. Set up/unpause the next flight, then press S.' if success and self.stream else ('Local session saved. Press S for another session.' if success else 'Finalization not confirmed; local data retained. See session.log.')
            if success and self.mode=='landing' and self.landing_cycle and self.aircraft is not None:
                self.state='WAIT_RESET';self.message='Flight saved. Waiting for reset to 3,000 ft MSL.'
        if self.state=='WAIT_RESET' and self.reset_pending and not self.exit_requested:
            self.start(now)
        if self.pause_sent is not None and not self.pause_confirmed and now-self.pause_sent>3:
            self.log('Pause not confirmed. Pause the simulator manually; recording has already stopped.')
            self.pause_sent=None
        if self.exit_requested and self.state not in {'SAVING','RECORDING','COUNTDOWN'}:self.closed=True
    def snapshot(self,now):
        flight=self.worker.flight if self.worker else None
        return {'state':self.state,'connected':self.connected(now),'signals':len(self.seen),
                'mode':self.mode or 'detecting','aircraft':(self.aircraft or {}).get('description','Waiting for UDP identity'),
                'remaining':(max(0,self.landing.touchdown+15-now) if self.landing.touchdown is not None else 15) if self.mode=='landing' else self.timer.remaining(now),'countdown':max(0,(self.countdown_until or now)-now),
                'dataset':flight.manifest['name'] if flight else '—',
                'uploaded':flight.manifest.get('confirmed_uploaded_packets',0) if flight else 0,
                'queue':self.worker.jobs.qsize() if self.worker else 0,
                'cloud_failed':bool(flight and flight.failed),'message':self.message,'events':list(self.events),
                'pause': 'confirmed' if self.pause_confirmed else ('requested' if self.pause_sent else '—')}
    def close(self):
        if self.record_file:self.record_file.close()
        self.log_file.close()


def controller_loop(controller):
    try:
        while not controller.closed:
            # Drain already-received samples before evaluating the deadline.
            for _ in range(100):
                try:event=controller.receiver.events.get_nowait()
                except queue.Empty:break
                controller.feed(*event)
            controller.tick(time.monotonic());time.sleep(.02)
    except Exception as exc:
        controller.log('Session controller error: '+type(exc).__name__)
        controller.stop(time.monotonic(),'controller error',pause=True)
        if controller.worker:controller.worker.done.wait()
        controller.state='ERROR';controller.message='Session error; local journals retained.';controller.closed=True


def draw_console(screen,controller):
    try:curses.curs_set(0)
    except curses.error:pass
    screen.nodelay(True)
    if curses.has_colors():
        curses.start_color();curses.use_default_colors()
        curses.init_pair(1,curses.COLOR_CYAN,-1);curses.init_pair(2,curses.COLOR_GREEN,-1)
        curses.init_pair(3,curses.COLOR_YELLOW,-1);curses.init_pair(4,curses.COLOR_RED,-1)
    while not controller.closed:
        status=controller.snapshot(time.monotonic());screen.erase();height,width=screen.getmaxyx()
        filled=int(30*(1-status['remaining']/controller.duration))
        bar=(' Waiting for touchdown; 15-second tail after first compression.' if status['mode']=='landing' else ' ['+'#'*filled+'-'*(30-filled)+']')
        lines=['FLIGHT SESSION RECORDER','='*min(72,max(0,width-1)),
               f" {status['state']}    X-Plane: {'CONNECTED' if status['connected'] else 'WAITING'}",
               f" Signals seen: {status['signals']}    Send pacing: >=1.05s after response",
               f" Mode: {status['mode']} | {status['aircraft']}",
               f" Session: {status['dataset']}",
               f" {'Starts in' if status['state']=='COUNTDOWN' else 'Time remaining'}: {status['countdown'] if status['state']=='COUNTDOWN' else status['remaining']:.1f}s    Pause: {status['pause']}",
               bar,
               f" Confirmed packets: {status['uploaded']}    Upload queue: {status['queue']}",'',
               ' '+status['message'],
               ' LOCAL CAPTURE ONLY — live upload failed; recovery runs at session end.' if status['cloud_failed'] else '',
               '', ' [S] Start recording & pushing data   [X] Stop & save   [Q] Quit safely','',
               ' Recent activity:',*[' '+line for line in status['events']], '',
               (' Landing mode: automatic 3,000-ft reset cycle; no automatic pause.' if status['mode']=='landing' else ' Timer: 60 wall-clock seconds. Dismiss X-Plane alerts promptly.')]
        for row,line in enumerate(lines[:max(0,height-1)]):
            style=curses.A_BOLD if row in [0,2] else 0
            if curses.has_colors():
                if row==0:style|=curses.color_pair(1)
                elif row==2:style|=curses.color_pair(4 if status['state']=='ERROR' else 2 if status['state'] in {'RECORDING','COMPLETE'} else 3)
            try:screen.addnstr(row,0,line,max(0,width-1),style)
            except curses.error:pass
        screen.refresh()
        try:key=screen.getch()
        except KeyboardInterrupt:key=ord('q')
        action={ord('s'):'start',ord('S'):'start',ord('x'):'stop',ord('X'):'stop',ord('q'):'quit',ord('Q'):'quit',3:'quit'}.get(key)
        if action:controller.commands.put(action)
        time.sleep(.1)


def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--host',default='127.0.0.1');ap.add_argument('--port',type=int,default=49000)
    ap.add_argument('--data-port',type=int,default=49005);ap.add_argument('--flush-seconds',type=float,default=1)
    ap.add_argument('--local-only',action='store_true');args=ap.parse_args()
    if args.flush_seconds<=0:ap.error('Upload interval must be positive')
    folder=Path('outputs/xplane')/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S-%fZ');folder.mkdir(parents=True)
    stream=None;minimum=1;minimum_numbers={}
    if not args.local_only:
        from xplane_marple import get_sdk_db
        db=get_sdk_db();streams=[s for s in db.get_streams() if s.name=='X-Plane Fair Live']
        stream=streams[0] if streams else db.create_stream('X-Plane Fair Live',type='realtime',description='Timed simulator sessions')
        datasets=list(stream.get_datasets())
        minimum_numbers={prefix:next_remote_number(datasets,prefix) for prefix in [CHALLENGE_PREFIX,ACROBATIC_PREFIX]}
    receiver=Receiver(args.host,args.port,10,folder,args.data_port)
    controller=SessionController(receiver,folder,stream,args.flush_seconds,auto_route=True);controller.minimum_numbers=minimum_numbers
    receiver.thread.start();thread=threading.Thread(target=controller_loop,args=(controller,),daemon=True);thread.start()
    try:curses.wrapper(draw_console,controller)
    except KeyboardInterrupt:pass
    finally:
        controller.commands.put('quit')
        # Curses is restored before waiting, so finalization remains visible.
        if thread.is_alive():print('Stopping safely; waiting for pending uploads and finalization…',flush=True)
        while thread.is_alive():
            try:thread.join(timeout=.5)
            except KeyboardInterrupt:print('Still finalizing; local capture is safe. Please wait.',flush=True)
        receiver.close();controller.close()
        print(controller.message);print('Logs:',folder)

if __name__=='__main__':
    try:main()
    except Exception as exc:
        print('Unable to run session console ('+type(exc).__name__+'). Check .env.local, network access and session.log.')
        raise SystemExit(1) from None
