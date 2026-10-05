const assert = require('node:assert/strict');
const {actionFor, handleKey} = require('../static/editor-shortcuts.js');
const event = (key, mods={}) => ({key, ctrlKey:false, metaKey:false, altKey:false, shiftKey:false, ...mods});
const card = {card:true, split:true, add:true, check:false};
for (const primary of ['ctrlKey', 'metaKey']) {
  for (const [key,shift,expected] of [['s',false,'save'],['f',false,'find'],['e',false,'export'],['z',false,'undo'],['Z',true,'redo'],['z',true,'redo'],['y',false,'redo'],['Enter',false,'split'],['Enter',true,'add']]) {
    const e=event(key,{[primary]:true,shiftKey:shift});
    assert.equal(actionFor(e,card),expected, `${primary} ${key}`);
    assert.equal(actionFor({...e,altKey:true},card),null);
    assert.equal(actionFor(e,{...card,modal:true}),null);
    assert.equal(actionFor({...e,isComposing:true},card),null);
  }
  for(const key of ['z','Z','y']) assert.equal(actionFor(event(key,{[primary]:true}),{...card,text:true}),null);
}
for (const [key,shift,expected] of [['m',false,'merge_next'],['M',true,'merge_previous'],['d',false,'delete'],[' ',false,'play']]) {
  assert.equal(actionFor(event(key,{ctrlKey:true,shiftKey:shift}),card),expected);
  assert.equal(actionFor(event(key,{metaKey:true,shiftKey:shift}),card),null);
}
assert.equal(actionFor(event('v',{ctrlKey:true,shiftKey:true}),{...card,text:true}),null);
assert.equal(actionFor(event('v',{ctrlKey:true,shiftKey:true}),{check:false}),null);
assert.equal(actionFor(event('ArrowDown',{altKey:true}),card),'next');
assert.equal(actionFor(event('ArrowUp',{altKey:true}),card),'previous');
assert.equal(actionFor(event('Escape'),card),'close');
assert.equal(actionFor(event('Escape',{shiftKey:true}),card),null);
for (const key of ['Enter','m','d']) assert.equal(actionFor(event(key,{ctrlKey:true}),{}),null);
assert.equal(actionFor(event('Enter',{ctrlKey:true,shiftKey:true}),{card:true,add:false}),null);
assert.equal(actionFor(event('Enter',{ctrlKey:true}),{card:true,split:false}),null);
for (const key of ['s','f','e','y','d',' ']) assert.equal(actionFor(event(key,{ctrlKey:true,shiftKey:true}),card),null);
assert.equal(actionFor(event('s',{ctrlKey:true,metaKey:true}),card),null);
assert.equal(actionFor(event('s'),card),null);

let emitted=[],prevented=0,stopped=0;
const field={value:'😀 one two',selectionStart:7,closest:()=>({getAttribute:()=> '3'})};
const video={paused:false,pause(){this.paused=true;},play(){this.paused=false;return Promise.resolve();}};
const doc={activeElement:{tagName:'TEXTAREA'},querySelector(selector){
 if(selector==='.caption-text-input textarea')return field;
 if(selector==='.caption-split-cursor')return {};
 if(selector==='video')return video;
 return null;
}};
const run=(key,mods={})=>handleKey({...event(key,mods),preventDefault(){prevented++;},stopPropagation(){stopped++;}},doc,(...args)=>emitted.push(args));
run('Enter',{ctrlKey:true});
assert.deepEqual(emitted.pop(),['editor-shortcut',{action:'split',caption_index:3,text:'😀 one two',cursor:6}]);
run('s',{ctrlKey:true});assert.equal(emitted.pop()[1].text,field.value);
const before=prevented;run('z',{ctrlKey:true});assert.equal(prevented,before);assert.equal(emitted.length,0);
run('d',{ctrlKey:true,repeat:true});assert.equal(emitted.length,0);assert.equal(prevented,before+1);
run(' ',{ctrlKey:true});assert.equal(video.paused,true);
run(' ',{ctrlKey:true});assert.equal(video.paused,false);assert.equal(emitted.length,0);
console.log('All shortcut mappings, guards, cursor capture, repeat handling and playback checks passed');
