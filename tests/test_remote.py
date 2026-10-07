# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Voltcraft Live contributors
#
# This file is part of Voltcraft Live.
# Voltcraft Live is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Voltcraft Live is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Voltcraft Live. If not, see <https://www.gnu.org/licenses/>.

import copy
import importlib.util
import json
import threading
import time
import unittest
import tempfile
import urllib.request
import urllib.error
from pathlib import Path
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parent.parent
spec=importlib.util.spec_from_file_location('v',ROOT/'voltcraft_live.py')
v=importlib.util.module_from_spec(spec);spec.loader.exec_module(v)

SETTINGS={"channels":{str(i):{"enabled":i==1,"coupling":"DC","probe":1,"scale":1.0,"offset":0.0,"bwlimit":False,"invert":False} for i in range(1,5)},
          "timebase":{"scale":.0002,"position":0.0},"trigger":{"sweep":"AUTO","source":"CHANnel1","slope":"RISING","level":0.0}}

class FakeUSB:
    instances=[]
    def __init__(self,path):
        self.settings=copy.deepcopy(SETTINGS);self.running=True;self.pending=[];self.calls=[];self.closed=False
        self.mode='EDGE';self.drop_scale=False;self.fail_write=None;self.locked=True
        FakeUSB.instances.append(self)
    def target(self,command):
        for path,wire,_ in v.setting_targets(self.settings):
            if wire==command:
                node=self.settings
                for p in path[:-1]:node=node[p]
                return node,path[-1]
        raise ValueError('Unknown command '+command)
    def write(self,command):
        assert not self.pending,'Control interleaved with waveform packets'
        self.calls.append(command)
        if self.fail_write==command:raise OSError('simulated USB failure')
        if command==':SYSTem:LOCKed OFF':self.locked=False
        elif command==':RUNning ON':self.running=True
        elif command==':STOP':self.running=False
        elif command==':AUToset':self.settings['timebase']['scale']=.001
        elif command==':TRIGger:FORCe':pass
        else:
            wire,argument=command.rsplit(' ',1)
            if wire==':TRIGger:MODE':self.mode=argument;return
            node,name=self.target(wire)
            if self.drop_scale and name=='scale':return
            if type(node[name]) is bool:node[name]=argument=='ON'
            elif type(node[name]) is int:node[name]=int(argument)
            elif type(node[name]) is float:node[name]=float(argument)
            else:node[name]=argument.upper() if name!='source' else argument
    def query(self,command):
        if command!='*IDN?':self.locked=True
        self.calls.append(command)
        if command in v.COMMANDS.values():
            if command==v.COMMANDS['display']:return b'#9000000000'
            if not self.pending:
                try:frame=v.demo_frame(0,self.settings,self.running)
                except v.NoMeasurement:return b'#9000000000'
                self.pending=[f'#9000000128{len(frame):09d}000000000'.encode()+frame[:99],
                              f'#9{len(frame)-99+29:09d}{len(frame):09d}000000099'.encode()+frame[99:]]
            return self.pending.pop(0)
        assert not self.pending,'Query interleaved with waveform packets'
        if command=='*IDN?':return b'voltcraft, DSO1084F, TEST, 2.0.0(20220414.0)'
        if command==':SYSTem:LOCKed?':return b'ON' if self.locked else b'OFF'
        if command==':RUNning?':return str(int(self.running)).encode()
        if command==':TRIGger:MODE?':return self.mode.encode()
        node,name=self.target(command[:-1]);value=node[name]
        if type(value) is bool:return str(int(value)).encode()
        if name=='scale' and command.startswith(':CHANnel'):
            raw=v.struct.unpack('<d',v.struct.pack('<Q',round(value*1e6)))[0]
            return f'{raw:.3e}'.encode()
        return str(value).encode()
    def close(self):self.closed=True


def wait(predicate,limit=4):
    deadline=time.monotonic()+limit
    while time.monotonic()<deadline:
        if predicate():return
        time.sleep(.01)
    raise AssertionError('Timed out')

class Remote(unittest.TestCase):
    def test_reject_nonfinite_and_command_injection_before_any_write(self):
        for patch in [{'channels':{'1':{'coupling':'DC;:STOP'}}},{'timebase':{'scale':float('nan')}},
                      {'channels':{'1':{'scale':.003}}},{'channels':{'7':{'enabled':True}}},
                      {'channels':{'1':{'enabled':1}}}]:
            with self.assertRaises(ValueError):v.normalize_settings(patch)
    def test_decode_firmware_denormal_without_relabelling_graph_as_volts(self):
        self.assertEqual(v.decode_setting(b'2.470e-317','scale'),5)
        self.assertEqual(v.decode_setting(b'-0.472','offset'),-.472)
        self.assertTrue(v.decode_setting(b'"ON"\x00\n','running'))
        self.assertEqual(v.view_frame(v.demo_frame(0))['amplitude_unit'],'signed_sample_code')
    def test_roundtrip_partial_settings_and_detect_silent_rejection(self):
        usb=FakeUSB('test')
        patch=v.normalize_settings({'channels':{'1':{'probe':10,'scale':2.,'enabled':True}},'trigger':{'slope':'FALLING','sweep':'NORMAL'}})
        result=v.apply_settings(usb,patch)
        self.assertEqual(result['mismatches'],[])
        self.assertLess(usb.calls.index(':CHANnel1:PROBe 10'),usb.calls.index(':CHANnel1:SCALe 2'))
        usb.drop_scale=True
        result=v.apply_settings(usb,{'channels':{'1':{'scale':5.}}})
        self.assertTrue(result['mismatches'])
    def test_profiles_persist_import_and_never_overwrite_corruption(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'profiles.json';store=v.Profiles(path)
            p={'name':'Radio <test>','settings':{'channels':{'1':{'scale':.1}}},'view':{'x_zoom':4,'x_start':.4,'y_zoom':8,'shown':[True,False,True,False]}}
            store.change('save',{'profile':p})
            self.assertEqual(v.Profiles(path).listing()[0]['view']['x_zoom'],4)
            with self.assertRaises(ValueError):store.change('save',{'profile':p})
            bundle={'format':'voltcraft-live-profiles-v1','profiles':[{'name':'Zweites','settings':{}}]}
            self.assertEqual(len(store.change('import',{'bundle':bundle})),2)
            self.assertEqual(len(store.change('delete',{'name':'Zweites'})),1)
            path.write_text('{kaputt')
            with self.assertRaises(ValueError):store.change('save',{'profile':p,'overwrite':True})
            self.assertEqual(path.read_text(),'{kaputt')
    def test_concurrent_profile_saves_preserve_both_updates(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'profiles.json';errors=[]
            def save(name):
                try:v.Profiles(path).change('save',{'profile':{'name':name}})
                except Exception as e:errors.append(e)
            a=threading.Thread(target=save,args=('A',));b=threading.Thread(target=save,args=('B',));a.start();b.start();a.join();b.join()
            self.assertFalse(errors)
            self.assertEqual({p['name'] for p in v.Profiles(path).listing()},{'A','B'})
    def test_worker_controls_while_paused_and_no_packet_interleaving(self):
        original=v.USB;v.USB=FakeUSB
        live=v.Live(SimpleNamespace(device='fake',replay=None,demo=False,interval=.1,command='auto'))
        live.thread.start()
        try:
            wait(lambda:live.sequence>=1)
            usb=FakeUSB.instances[-1]
            wait(lambda:not usb.locked)
            jid=live.submit({'action':'unlock'})
            wait(lambda:live.job(jid)['status']=='done')
            self.assertFalse(live.active.is_set())
            wait(lambda:not usb.locked)
            time.sleep(.2)
            self.assertEqual(usb.calls[-1],':SYSTem:LOCKed OFF')
            self.assertNotIn(':SYSTem:LOCKed?',usb.calls)
            live.active.clear()
            jid=live.submit({'action':'stop'})
            wait(lambda:live.job(jid)['status'] in ('done','error'))
            self.assertEqual(live.job(jid)['status'],'done')
            wait(lambda:live.state()['frame']['running'] is False)
            jid=live.submit({'action':'apply','settings':{'channels':{str(i):{'enabled':False} for i in range(1,5)}}})
            wait(lambda:live.job(jid)['status']=='done')
            wait(lambda:bool(live.state()['error']))
            self.assertTrue(live.state()['remote_available'])
            jid=live.submit({'action':'apply','settings':{'channels':{'1':{'enabled':True}}}})
            wait(lambda:live.job(jid)['status']=='done')
            wait(lambda:live.state()['error']=='')
            jid=live.submit({'action':'single'})
            wait(lambda:live.job(jid)['status']=='done')
            self.assertEqual(live.job(jid)['result']['settings']['trigger']['sweep'],'SINGLE')
            usb=FakeUSB.instances[-1]
            self.assertIn(':TRIGger:SWEep SINGle',usb.calls)
            usb.fail_write=':CHANnel1:OFFSet 0.5'
            jid=live.submit({'action':'apply','settings':{'channels':{'1':{'offset':.5}}}})
            wait(lambda:live.job(jid)['status']=='error')
            self.assertEqual(usb.calls.count(':CHANnel1:OFFSet 0.5'),1)
        finally:
            live.stop.set();live.wake.set();live.thread.join(3);v.USB=original
        self.assertFalse(live.thread.is_alive())
    def test_http_jobs_profiles_auth_and_pause(self):
        with tempfile.TemporaryDirectory() as folder:
            live=v.Live(SimpleNamespace(device='fake',replay=None,demo=True,interval=.1,command='auto'))
            server=v.http.server.ThreadingHTTPServer(('127.0.0.1',0),v.handler(live,'testkey',v.Profiles(Path(folder)/'profiles.json')))
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();live.thread.start()
            base=f'http://127.0.0.1:{server.server_port}'
            def request(path,body=None,key='testkey',host=None):
                headers={'Content-Type':'application/json','X-Voltcraft-Key':key}
                if host:headers['Host']=host
                r=urllib.request.Request(base+path,data=json.dumps(body).encode() if body else None,headers=headers)
                return json.loads(urllib.request.urlopen(r,timeout=3).read())
            try:
                wait(lambda:live.sequence>0)
                with self.assertRaises(urllib.error.HTTPError) as e:request('/device',{'action':'stop'},key='wrong')
                self.assertEqual(e.exception.code,403)
                with self.assertRaises(urllib.error.HTTPError) as e:request('/state',host='example.invalid')
                self.assertEqual(e.exception.code,403)
                self.assertTrue(request('/control',{'paused':True})['paused'])
                job=request('/device',{'action':'read'})
                wait(lambda:request('/job/'+job['job_id'])['status']=='done')
                profile={'name':'Testprofil','settings':{'timebase':{'scale':.001}},'view':{'x_zoom':2}}
                request('/profiles',{'action':'save','profile':profile})
                self.assertEqual(request('/profiles.json')['profiles'][0]['name'],'Testprofil')
                self.assertTrue(request('/capture.json')['capture_complete'])
            finally:
                live.stop.set();live.wake.set();server.shutdown();server.server_close();thread.join(3);live.thread.join(3)

if __name__=='__main__':unittest.main()
