// SPDX-License-Identifier: GPL-3.0-or-later
// Copyright (C) 2026 Voltcraft Live contributors
// See ../LICENSE for license terms and warranty disclaimer.
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const {createCanvas}=require('@napi-rs/canvas');
const {execFileSync}=require('child_process');
const path=require('path');
const root=path.resolve(__dirname,'..');
const html=execFileSync(process.env.PYTHON||'python3',[path.join(__dirname,'ui_fixture.py'),'html'],{encoding:'utf8'});
const ids={},all=[],timers=[],posts=[];
class Option{constructor(text='',value=''){this.textContent=text;this.value=String(value);this.tagName='OPTION'}}
class Element{
 constructor(tag='div',id=''){this.tagName=tag.toUpperCase();this.id=id;this.style={};this.dataset={};this.children=[];this.options=[];this.events={};this._value='';this.disabled=false;this.checked=false;this.textContent='';this.type='';all.push(this);if(id)ids[id]=this}
 get value(){return this._value}set value(v){v=String(v);this._value=this.tagName==='SELECT'&&!this.options.some(x=>x.value===v)?'':v}
 add(o){this.options.push(o);if(this.options.length===1)this._value=o.value}
 replaceChildren(...xs){this.children=[];if(this.tagName==='SELECT'){this.options=[];this._value='';for(const x of xs)this.add(x)}else this.children=xs}
 append(...xs){this.children.push(...xs)}
 set innerHTML(v){parse(v)}
 addEventListener(name,callback,opts){this.events[name]=callback;if(name==='wheel')assert.equal(opts.passive,false)}
 getBoundingClientRect(){return {width:1100,height:470,left:0,top:0,right:1100,bottom:470}}
 checkValidity(){return this.type!=='number'||this.value===''||Number.isFinite(Number(this.value))}
 setAttribute(name,value){this[name]=value}
 setPointerCapture(){}click(){this.onclick?.()}toBlob(fn){fn({})}
}
function attr(s,name){return new RegExp('(?:^|\\s)'+name+'="([^"]*)"').exec(s)?.[1]}
function parse(source){for(const m of source.matchAll(/<(input|select|button|canvas|div|span|p|tbody|a)\b([^>]*)>/g)){
 const id=attr(m[2],'id'),device=attr(m[2],'data-device');if(!id&&!device)continue;let e=id&&ids[id];if(!e)e=new Element(m[1],id||'');if(device)e.dataset.device=device;e.type=attr(m[2],'type')||'';if(m[1]==='select'){let block=source.slice(m.index+m[0].length).split('</select>')[0];e.options=[];for(const om of block.matchAll(/<option\b([^>]*)>([^<]*)<\/option>/g))e.add(new Option(om[2],attr(om[1],'value')??om[2]))}
}}
parse(html);const canvas=createCanvas(1100,470),plot=ids.plot;
Object.defineProperty(plot,'width',{get:()=>canvas.width,set:v=>canvas.width=v});Object.defineProperty(plot,'height',{get:()=>canvas.height,set:v=>canvas.height=v});plot.getContext=()=>canvas.getContext('2d');
const state=JSON.parse(execFileSync(process.env.PYTHON||'python3',[path.join(__dirname,'ui_fixture.py'),'state'],{encoding:'utf8'}));
const profile={name:'Radio <safe>',settings:{channels:{'1':{enabled:true,scale:.1,coupling:'AC',probe:10}},timebase:{scale:.001},trigger:{sweep:'AUTO'}},view:{x_zoom:4,x_start:.2,y_zoom:8,y_center:0,shown:[true,false,true,false]}};
const document={getElementById:id=>{assert(ids[id],'Unknown ID '+id);return ids[id]},createElement:tag=>new Element(tag),querySelectorAll:q=>q==='[data-device]'?all.filter(e=>e.dataset.device):q==='#device-actions button'?all.filter(e=>['run','stop','single','auto','force','read','unlock'].includes(e.dataset.device)):[]};
const fetch=async(path,opts)=>{if(opts?.method==='POST')posts.push({path,body:JSON.parse(opts.body)});let data=path==='/state'?state:path==='/profiles'?{profiles:[profile]}:path.startsWith('/job/')?{id:'job',status:'done',message:'OK',result:{settings:profile.settings}}:path==='/device'?{job_id:'job'}:{};return {ok:true,status:200,json:async()=>data}};
const context=vm.createContext({document,window:{devicePixelRatio:1,addEventListener(){},confirm(){return true}},Option,fetch,console,URL:{createObjectURL:()=>'',revokeObjectURL(){}},setTimeout:fn=>{timers.push(fn);return 1}});
const js=html.split('<script>')[1].split('</script>')[0].replace('__TOKEN__','"test"');
vm.runInContext(js,context);
function read(expr){return vm.runInContext(expr,context)}
function wheel(extra={}){let prevented=false;plot.events.wheel({clientX:600,clientY:200,deltaY:-100,deltaMode:0,shiftKey:false,preventDefault(){prevented=true},...extra});return prevented}
(async()=>{
 await new Promise(r=>setImmediate(r));assert.equal(ids.badge.textContent,'Live');
 assert.equal(ids.legend.children.length,4);assert.equal(ids.legend.children[0].children[0].disabled,false);assert.equal(ids.legend.children[1].children[0].disabled,true);assert.equal(ids.legend.children[1].children[0].checked,false);
 vm.runInContext('renderLegend(null)',context);assert.equal(ids.legend.children.length,4);assert(ids.legend.children.every(label=>label.children[0].disabled));
 vm.runInContext('render(state)',context);assert.equal(ids.legend.children.length,4);
 vm.runInContext('render({...state,frame:{...state.frame,channels:[...state.frame.channels,{channel:2,points:[],count:4000}]}})',context);assert.equal(ids.legend.children[1].children[0].disabled,false);assert.equal(ids.legend.children[1].children[0].checked,true);
 vm.runInContext('render({...state,frame:{...state.frame,channels:state.frame.channels.filter(ch=>ch.channel===1)}})',context);assert.equal(ids.legend.children[1].children[0].disabled,true);

 const p=(600-66)/(1100-84),anchorBefore=read('view.x_start')+p/read('view.x_zoom');
 assert(wheel());assert(read('view.x_zoom')>1);let anchorAfter=read('view.x_start')+p/read('view.x_zoom');assert(Math.abs(anchorBefore-anchorAfter)<1e-12);
 const py=(200-28)/(470-76),verticalBefore=read('view.y_center')+(1-2*py)*128/read('view.y_zoom');wheel({shiftKey:true});let verticalAfter=read('view.y_center')+(1-2*py)*128/read('view.y_zoom');assert(Math.abs(verticalBefore-verticalAfter)<1e-10);
 for(let i=0;i<70;i++){wheel();wheel({shiftKey:true})}assert(read('view.x_zoom')<=512);assert(read('view.y_zoom')<=64);
 assert(!wheel({clientX:2}));let start=read('view.x_start');plot.events.pointerdown({button:0,clientX:600,clientY:200,pointerId:1});plot.events.pointermove({clientX:400,clientY:220});plot.events.pointerup({});assert(read('view.x_start')>=start);
 plot.events.dblclick();assert.equal(read('view.x_zoom'),1);assert.equal(read('view.y_zoom'),1);assert.equal(read('view.y_center'),0);
 let count=posts.length;ids['profile-select'].value=profile.name;ids['profile-select'].onchange();assert.equal(posts.length,count,'Selecting a profile must never change the device');assert.equal(ids['ch1-scale'].value,'.1'.replace(/^\./,'0.'));assert.equal(ids['ch1-probe'].value,'10');assert.equal(read('view.x_zoom'),4);assert.equal(ids['profile-name'].value,profile.name);
 await ids['save-profile'].onclick();assert.equal(posts.at(-1).path,'/profiles');assert.equal(posts.at(-1).body.action,'save');assert.equal(posts.at(-1).body.profile.settings.channels['1'].scale,.1);
 ids['apply-settings'].onclick();await new Promise(r=>setImmediate(r));assert.equal(posts.at(-1).path,'/device');assert.equal(posts.at(-1).body.action,'apply');assert.equal(posts.at(-1).body.settings.channels['1'].probe,10);
 document.querySelectorAll('#device-actions button').find(b=>b.dataset.device==='unlock').onclick();await new Promise(r=>setImmediate(r));assert.equal(posts.at(-1).body.action,'unlock');
 ids['ch1-offset'].value='1.5';assert.equal(read('getSettings().channels["1"].offset'),1.5);
 ids['zoom'].value='32';ids['zoom'].onchange();assert.equal(read('view.y_zoom'),32);
 ids['clear-settings'].onclick();assert.deepEqual(JSON.parse(JSON.stringify(read('getSettings()'))),{});
 ids.pause.onclick();await new Promise(r=>setImmediate(r));assert.equal(posts.at(-1).path,'/control');
 assert(canvas.toBuffer('image/png').length>1000);
 console.log('UI passed: anchored wheel zoom, limits, drag, reset, profile selection/save, remote apply, form values, pause, native Canvas render.');
})().catch(e=>{console.error(e);process.exitCode=1});
