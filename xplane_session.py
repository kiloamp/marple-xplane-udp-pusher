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
                         LandingCut, AltitudeReset, next_remote_number, reserve_challenge_name,
                         command_packet)
from xplane_aircraft import aircraft_mode, identity_key
from xplane_sampling import LiveSampler, ReportTelemetry, SAMPLE_MODES, LIVE_SIGNALS
from xplane_file_upload import FileUploadWorker, ParticipantUploadWorker, file_stream
from xplane_tui import ConsoleInput, console_lines
from xplane_reset import LandingReset, POSITION_FIELDS, fresh_values, reset_packet

ACROBATIC_PREFIX = "Marple_Acrobatic_"
LANDING_TAIL_SECONDS = 10.0
LANDING_END_REASON = '10 seconds after first gear compression'


def alert_packet(message):
    lines=message.splitlines()
    if len(lines)>4 or any(len(s.encode('utf-8'))>239 for s in lines):
        raise ValueError('ALRT accepts four lines of at most 239 UTF-8 bytes')
    return b'ALRT\0'+struct.pack('<240s240s240s240s',*[s.encode() for s in (lines+['']*4)[:4]])


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
            self.flight=Flight(self.folder,self.number,self.stream,f'{self.name}.live',log=self.log,metadata=self.metadata)
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
    def __init__(self,receiver,folder,stream=None,interval=1,duration=60,delay=5,auto_route=False,sample_mode='low',analysis_stream=None):
        self.receiver=receiver;self.folder=folder;self.stream=stream;self.interval=interval
        self.analysis_stream=analysis_stream;self.file_worker=None;self.file_workers=[]
        self.completed_files=[];self.participant_jobs=[]
        self.auto_route=auto_route;self.mode=None if auto_route else 'timed';self.aircraft=None
        self.sample_mode=sample_mode;self.active_sample_mode=sample_mode
        self.sampler=LiveSampler(sample_mode);self.report_telemetry=ReportTelemetry()
        self.requested_start=False;self.landing_cycle=False;self.reset_pending=False
        self.landing=LandingCut(LANDING_TAIL_SECONDS);self.reset_watch=AltitudeReset();self.minimum_numbers={}
        self.sequence_path=Path('outputs/xplane/challenge-sequence.json')
        self.duration=duration;self.delay=delay;self.state='READY';self.message='Press S to start recording & pushing data.'
        self.latest={};self.seen=set();self.last_packet=None;self.worker=None;self.number=0
        self.countdown_until=None;self.timer=SessionTimer(duration);self.events=[]
        self.exit_requested=False
        self.approach_start=None;self.resetter=LandingReset(receiver,self.log)
        self.record_file=None;self.record_path=None;self.session_manifest={};self.commands=queue.Queue();self.closed=False
        self.log_file=(folder/'session.log').open('a',buffering=1)
    def route(self,now):
        if not self.auto_route:return
        identity=self.receiver.identity.snapshot(now)
        if identity is None:
            if self.resetter.active:return  # PREL can briefly interrupt identity while loading.
            if self.state in {'RECORDING','COUNTDOWN','WAITING'}:
                self.stop(now,'aircraft identity unavailable')
            self.aircraft=None
            if self.state not in {'SAVING','ERROR'}:
                self.state='WAIT_ID';self.message='Waiting for confirmed aircraft identity over UDP…'
            return
        if self.aircraft is not None and identity_key(identity)==identity_key(self.aircraft):return
        self.resetter.cancel()
        if self.state in {'RECORDING','COUNTDOWN','WAITING'}:
            self.stop(now,'aircraft changed')
        if self.state=='SAVING':return  # Finish the old dataset before applying new metadata.
        self.aircraft=dict(identity);self.mode=aircraft_mode(identity)
        self.landing=LandingCut(LANDING_TAIL_SECONDS);self.reset_watch=AltitudeReset();self.reset_pending=False
        self.state='READY';self.landing_cycle=False
        label=identity.get('description') or identity.get('icao')
        self.log(f'Aircraft: {label}; selected {self.mode} flow.')
        self.message='Press S for a 60-second Marple Acrobatic session.'
        if self.mode=='landing' or self.requested_start:self.start(now)

    def begin_recording(self,now):
        self.sampler=LiveSampler(self.active_sample_mode);self.report_telemetry=ReportTelemetry()
        self.timer.start(now);self.state='RECORDING'
        self.message='Recording landing; waiting for first gear compression.' if self.mode=='landing' else 'START — fly for 60 seconds.'
        self.record_path=self.folder/f'session-{self.number:03d}.jsonl'
        self.record_file=self.record_path.open('x',buffering=1)
        self.session_manifest={'state':'CAPTURING','name':self.worker.name,
            'dataset_id':self.worker.flight.manifest.get('dataset_id'),
            'mode':self.mode,'aircraft':self.aircraft,
            'live_sample_hz':self.sampler.hz,'local_capture_hz':10,
            'timer':LANDING_END_REASON if self.mode=='landing' else '60 wall-clock seconds',
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
        if self.resetter.active:return
        if self.auto_route and self.aircraft is None:
            self.requested_start=True;self.state='WAIT_ID';return
        self.requested_start=False
        if self.mode=='landing':
            self.landing_cycle=True;self.landing=LandingCut(LANDING_TAIL_SECONDS);self.reset_watch=AltitudeReset();self.reset_pending=False
        self.record_path=None;self.session_manifest={}
        self.file_worker=None
        self.approach_start=None;self.resetter=LandingReset(self.receiver,self.log)
        self.worker=None;self.state='WAITING';self.message='Waiting for live, unpaused X-Plane telemetry…'
        self.countdown_until=None;self.timer=SessionTimer(self.duration)
    def prepare(self,now):
        self.number+=1
        namespace=f'stream:{self.stream.id}' if self.stream else 'local'
        # Remote maximum was read at connection setup; reservations persist across restarts.
        prefix=CHALLENGE_PREFIX if self.mode=='landing' else ACROBATIC_PREFIX
        minimum=self.minimum_numbers.get(prefix,getattr(self,'minimum_number',1) if self.mode=='landing' else 1)
        if self.mode!='landing':namespace+=':acrobatic'
        name=reserve_challenge_name(self.sequence_path,namespace,minimum,prefix=prefix)
        self.active_sample_mode=self.sample_mode
        metadata={**FLIGHT_METADATA,'A/C Model':'Airbus A320' if self.mode=='landing' else 'Marple Acrobatic',
                  'Live Sample Rate Hz':SAMPLE_MODES[self.active_sample_mode], 'Local Capture Rate Hz':10,
                  'Capture Type':'Live'}
        if self.aircraft:
            metadata.update({'X-Plane Aircraft':self.aircraft.get('description',''),
                             'X-Plane ICAO':self.aircraft.get('icao',''),'Session Mode':self.mode})
        self.worker=UploadWorker(self.folder,self.number,name,self.stream,self.interval,self.log,metadata=metadata)
        self.worker.start();self.countdown_until=now+(0 if self.mode=='landing' else self.delay);self.state='COUNTDOWN'
        self.message='Get ready — recording starts after the countdown.'
    def start_file_upload(self,reason):
        if self.file_worker is not None or self.record_path is None:return
        if self.record_file:self.record_file.flush()
        if not self.record_path.stat().st_size:return
        metadata={**self.worker.metadata,'Capture End':reason,
                  'Capture Type':'SDK upload','Session':self.worker.name}
        metadata.pop('Live Sample Rate Hz',None)
        self.file_worker=FileUploadWorker(self.record_path,self.record_path.stat().st_size,
            self.number,self.worker.name,self.analysis_stream,metadata,self.log)
        self.file_workers.append(self.file_worker)
        self.session_manifest['analysis_manifest']=str(self.file_worker.manifest_path)
        self.save_session()
        self.file_worker.start()
        self.log('Starting full-rate SDK file upload; realtime continues independently.')

    def stop(self,now,reason='operator stop'):
        if reason=='operator stop':
            self.landing_cycle=False;self.requested_start=False;self.reset_pending=False
            self.resetter.cancel()
        if self.state in {'WAIT_ID','WAIT_RESET'}:
            self.state='READY';self.message='Stopped. Press S to start again.'
        if self.state=='RECORDING':
            self.start_file_upload(reason)
            if self.file_worker is not None:
                self.completed_files.append({'id':str(self.file_worker.manifest_path),
                    'worker':self.file_worker,'job':None})
            self.state='SAVING';self.message='Recording stopped. Uploading remaining data and finalizing Marple…'
            if self.record_file:self.record_file.close();self.record_file=None
            self.session_manifest.update(state='LOCAL_CAPTURE_COMPLETE',end_reason=reason,capture_complete=True)
            self.save_session()
            self.worker.finish(reason)
            if self.mode=='landing' and reason==LANDING_END_REASON:
                self.resetter.begin(now,self.latest,self.approach_start)
                self.save_reset_status()
        elif self.state in {'WAITING','COUNTDOWN'}:
            if self.worker:
                self.worker.finish('cancelled before recording');self.state='SAVING'
            else:self.state='READY'
            self.message='Start cancelled.'
    def feed(self,timestamp,mono,values):
        touchdown=False
        self.route(mono)
        if self.state=='RECORDING':values=self.report_telemetry.add(mono,values)
        self.last_packet=mono;self.seen.update(values)
        self.latest.update({k:(v,mono) for k,v in values.items()})
        if self.mode=='landing' and self.state=='RECORDING' and self.approach_start is None:
            status=fresh_values(self.latest,mono,('paused','replay','on_ground'))
            target=fresh_values(self.latest,mono,POSITION_FIELDS)
            if status=={'paused':0,'replay':0,'on_ground':0} and target:
                try:reset_packet(target)
                except ValueError:pass
                else:
                    self.approach_start=target
                    self.session_manifest['approach_start']=target;self.save_session()
        if self.mode=='landing' and self.aircraft is not None:
            if self.reset_watch.observe(values,mono):
                self.reset_pending=True
                self.log('3,000 ft reset detected. Next landing capture starts when saving finishes and X-Plane is unpaused.')
                if self.state=='RECORDING':self.stop(mono,'reset before landing cutoff')
            if self.state=='RECORDING':
                previous=self.landing.touchdown;self.landing.observe(mono,values)
                if previous is None and self.landing.touchdown is not None:
                    touchdown=True
                    self.reset_watch.landed=True
                    self.session_manifest.update(touchdown_time_ns=timestamp,cutoff_time_ns=timestamp+int(self.landing.tail*1e9))
                    # Preserve a fresh touchdown snapshot even in 1 Hz live mode.
                    fields=['latitude_deg','longitude_deg','airspeed_kias','groundspeed_kt',
                            'vertical_speed_fpm','pitch_deg','roll_deg','normal_g','heading_magnetic_deg',
                            'flap_deploy_ratio','throttle_lever_1_ratio','throttle_lever_2_ratio']
                    self.session_manifest['touchdown_snapshot']={name:self.latest[name][0] for name in fields
                        if name in self.latest and mono-self.latest[name][1]<.5}
                    self.save_session();self.log(f'First gear compression: uploading analysis file; pause and 3,000 ft reset in {self.landing.tail:g} seconds.')
                if self.landing.expired(mono):self.stop(mono,LANDING_END_REASON)
        in_window=(self.mode=='landing' or (self.timer.started is not None and mono<self.timer.started+self.duration))
        if self.state=='RECORDING' and self.timer.started<=mono and in_window:
            # Separate journal remains authoritative even if the cloud worker fails.
            self.record_file.write(json.dumps({'time':timestamp,**values})+'\n')
            # Freeze the full journal AFTER including the first compression sample.
            if touchdown:self.start_file_upload('first gear compression')
            selected=self.sampler.select(mono,{k:v for k,v in values.items() if k in LIVE_SIGNALS})
            if selected and not self.worker.done.is_set():
                try:self.worker.jobs.put_nowait((timestamp,selected))
                except queue.Full:
                    self.log('Upload queue full; ending session. Complete controller journal retained.')
                    self.stop(time.monotonic(),'upload queue full')
    def save_reset_status(self):
        self.session_manifest['automatic_reset']={'state':self.resetter.state,'message':self.resetter.message}
        self.save_session()
    def set_participant(self, target, name):
        entry=next((e for e in self.completed_files if e['id']==target),None)
        if entry is None or (entry['job'] and not entry['job'].done.is_set()):return
        try:job=ParticipantUploadWorker(entry['worker'],name)
        except ValueError:return
        entry['job']=job;self.participant_jobs.append(job);job.start()
    def tick(self,now):
        self.route(now)
        while not self.commands.empty():
            key=self.commands.get_nowait()
            if isinstance(key,tuple) and len(key)==3 and key[0]=='participant':
                self.set_participant(key[1],key[2])
            elif key=='start':self.start(now)
            elif key=='stop':self.stop(now)
            elif key=='quit':self.exit_requested=True;self.stop(now)
            elif key=='rate':
                self.sample_mode='high' if self.sample_mode=='low' else 'low'
                self.log(f'Next flight live sampling: {self.sample_mode.upper()} ({SAMPLE_MODES[self.sample_mode]} Hz). Current dataset keeps its original rate.')
        if self.receiver.failure:
            self.resetter.cancel()
            self.log('UDP receiver failed: '+self.receiver.failure);self.stop(now,'UDP failure')
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
                if self.landing.expired(now):self.stop(now,LANDING_END_REASON)
            else:
                for event in self.timer.tick(now):
                    if event=='warning':self.send_alert('10 seconds to go')
                    elif event=='stop':self.stop(now,'60-second session complete')
        old_reset_state=self.resetter.state
        self.resetter.update(now,self.latest)
        if old_reset_state!=self.resetter.state:
            self.save_reset_status()
            if self.resetter.state=='COMPLETE':
                self.reset_pending=True;self.reset_watch.landed=False
        if self.state=='SAVING' and self.worker and self.worker.done.is_set():
            manifest=self.worker.flight.manifest if self.worker.flight else {}
            success=not self.worker.error and manifest.get('state') in {'FINISHED','LOCAL_CAPTURE_COMPLETE'} and not manifest.get('upload_status')
            self.state='COMPLETE' if success else 'ERROR'
            self.message='Realtime ended. Open the SDK analysis file once its status is FINISHED. Press S for another session.' if success and self.stream else ('Local session saved. Press S for another session.' if success else 'Live finalization failed. SDK analysis file has its own status above; local data retained.')
            if self.mode=='landing' and self.landing_cycle and self.aircraft is not None:
                self.state='WAIT_RESET';self.message='Realtime ended. Use the SDK upload for analysis; reset to 3,000 ft for the next flight.'
                if not success:self.message='Live finalization failed; SDK file has its own status above. Waiting for reset to 3,000 ft.'
        if self.state=='WAIT_RESET' and self.reset_pending and not self.exit_requested and not self.resetter.active:
            self.start(now)
            self.message='3,000 ft reset detected. Finish ISCS setup, then unpause to start the next recording.'
        if self.exit_requested and self.state not in {'SAVING','RECORDING','COUNTDOWN'}:
            if all(w.done.is_set() for w in self.file_workers+self.participant_jobs):self.closed=True
            else:self.message='Waiting for SDK file uploads before exiting…'
    def snapshot(self,now):
        flight=self.worker.flight if self.worker else None
        analysis=self.file_worker or (self.file_workers[-1] if self.file_workers else None)
        return {'state':self.state,'connected':self.connected(now),'signals':len(self.seen),
                'exiting':self.exit_requested,'reset_state':self.resetter.state,
                'completed':[{'id':e['id'],'name':e['worker'].manifest['name'],
                    'participant':e['job'].name if e['job'] else '',
                    'name_status':e['job'].state if e['job'] else 'UNNAMED'} for e in self.completed_files],
                'analysis_name':analysis.manifest['name'] if analysis else '',
                'analysis_state':analysis.manifest['state'] if analysis else '',
                'live_signals':len(self.sampler.last_bucket),'touchdown':self.landing.touchdown is not None,
                'sample_mode':self.sample_mode,'active_sample_mode':self.active_sample_mode,
                'mode':self.mode or 'detecting','aircraft':(self.aircraft or {}).get('description','Waiting for UDP identity'),
                'remaining':(max(0,self.landing.touchdown+self.landing.tail-now) if self.landing.touchdown is not None else self.landing.tail) if self.mode=='landing' else self.timer.remaining(now),'countdown':max(0,(self.countdown_until or now)-now),
                'dataset':flight.manifest['name'] if flight else '—',
                'analysis':(f"{analysis.manifest['state']} | dataset={analysis.manifest.get('dataset_id','—')} | {analysis.manifest['name']}" if analysis else 'Waiting for touchdown' if self.mode=='landing' else 'Waiting for session end'),
                'uploaded':flight.manifest.get('confirmed_uploaded_packets',0) if flight else 0,
                'queue':self.worker.jobs.qsize() if self.worker else 0,
                'cloud_failed':bool(self.worker and (self.worker.error or (flight and flight.failed))),'message':self.message,'events':list(self.events),
                'pause': self.resetter.message if self.mode=='landing' else 'automatic pause disabled'}
    def close(self):
        if self.record_file:self.record_file.close()
        for worker in self.file_workers+self.participant_jobs:
            while worker.is_alive():worker.join(timeout=.5)
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
        controller.stop(time.monotonic(),'controller error')
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
    editor=ConsoleInput()
    while not controller.closed:
        status=controller.snapshot(time.monotonic())
        editor.observe(status)
        screen.erase();height,width=screen.getmaxyx()
        lines=console_lines(status,editor)
        for row,line in enumerate(lines[:max(0,height-1)]):
            style=curses.A_BOLD if row in (0,2) else 0
            if curses.has_colors() and row==0:style|=curses.color_pair(1)
            try:screen.addnstr(row,0,line,max(0,width-1),style)
            except curses.error:pass
        screen.refresh()
        try:key=screen.get_wch()
        except curses.error:key=None
        except KeyboardInterrupt:key='\x03'
        action=editor.handle(key,status)
        if action:controller.commands.put(action)
        time.sleep(.1)


def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--host',default='127.0.0.1');ap.add_argument('--port',type=int,default=49000)
    ap.add_argument('--data-port',type=int,default=49005);ap.add_argument('--flush-seconds',type=float,default=1)
    ap.add_argument('--sample-mode',choices=['low','high'],default='low')
    ap.add_argument('--include-data',action='store_true',help='Opt in to legacy checkbox-selected DATA capture')
    ap.add_argument('--local-only',action='store_true');args=ap.parse_args()
    if args.flush_seconds<=0:ap.error('Upload interval must be positive')
    folder=Path('outputs/xplane')/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S-%fZ');folder.mkdir(parents=True)
    stream=None;analysis_stream=None;minimum=1;minimum_numbers={}
    if not args.local_only:
        from xplane_marple import get_sdk_db
        db=get_sdk_db();streams=[s for s in db.get_streams() if s.name=='X-Plane Fair Live']
        stream=streams[0] if streams else db.create_stream('X-Plane Fair Live',type='realtime',description='Timed simulator sessions')
        analysis_stream=file_stream(db)
        datasets=list(stream.get_datasets())+list(analysis_stream.get_datasets())
        minimum_numbers={prefix:next_remote_number(datasets,prefix) for prefix in [CHALLENGE_PREFIX,ACROBATIC_PREFIX]}
    receiver=Receiver(args.host,args.port,10,folder,args.data_port,include_data=args.include_data)
    controller=SessionController(receiver,folder,stream,args.flush_seconds,auto_route=True,sample_mode=args.sample_mode,analysis_stream=analysis_stream);controller.minimum_numbers=minimum_numbers
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
