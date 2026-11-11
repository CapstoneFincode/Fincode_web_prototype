import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFileSync} from 'node:fs';
import {webcrypto} from 'node:crypto';
import * as seeds from '../public/data.js';
import * as categoryTools from '../public/categories.js';

const source=readFileSync(new URL('../public/app.js',import.meta.url),'utf8')
  .replace(/^import[^\n]+\n/gm,'')
  .replace(/^/,'const {historicalRuns,attackSeeds,normalSeeds,historicalTypes}=seeds;const {categorySeeds,categoryNameKey,categoryFor,normalizeCategoryData,categoryGroups,categoryScopes,categoryScope,categoryLayout,selectionPlan,attackVectors,vectorCategoryKey,vectorSelectionPlan}=categoryTools;\n')
  .replace('export function validateImport','function validateImport')
  .replace(/route\(\);\s*refreshConnection\(\);\s*$/,'');
const id='c42274af-3449-4d09-885c-84329d5c76b4';
const secondId='d8e2c716-7c67-4929-94c9-9ba0548e4c45';
const answer=(body,status=200)=>({ok:status<400,status,json:async()=>body});
const attachment=(changes={})=>({id,filename:'회의.hwp',sizeBytes:123,text:'표 안의 한글 😀\u200b',textChars:Array.from('표 안의 한글 😀\u200b').length,method:'hwp-xml',extractorVersion:'test',status:'ready',warnings:[],...changes});
const file=(name='회의.hwp',size=123)=>({name,size,originalFile:true});
function setup(fetcher=async()=>{throw Error('Unexpected request');}){
  const input=(value='')=>({value,listeners:{},addEventListener(type,handler){this.listeners[type]=handler;}});
  const form={...input(),values:{name:'첨부파일 실험',categoryId:'attachment-injection',vector:'indirect',channel:'attachment',technique:'override',goal:'marker',payload:'',criterion:'표식 출력',evaluationMode:'manual',expectedOutput:''}};
  const elements={'#modal':{open:false,showModal(){this.open=true;},close(){this.open=false;}},'#record-form':form,'#form-error':{},'#f-evaluationMode':input('manual'),'#f-expectedOutput':{},'#expected-output-field':{},'#evaluation-hint':{}};
  const calls=[],writes=[],toasts=[];
  class FormDataStub{
    constructor(form){this.values=new Map(Object.entries(form?.values||{}));}
    append(name,value){this.values.set(name,value);}
    get(name){return this.values.get(name);}
    [Symbol.iterator](){return this.values[Symbol.iterator]();}
  }
  const context=vm.createContext({categoryTools,seeds,structuredClone,crypto:webcrypto,console,setTimeout,clearTimeout,AbortController,FormData:FormDataStub,
    fetch:async(path,options)=>{calls.push({path,options});return fetcher(path,options);},
    localStorage:{getItem:()=>null,setItem:(key,value)=>writes.push(value)},
    document:{querySelector:s=>elements[s]||null,querySelectorAll:()=>[],addEventListener(){}},window:{addEventListener(){},scrollTo(){}},location:{hash:'#email'},toasts});
  vm.runInContext(source+'\nshell=()=>{};renderRuntime=()=>{};toast=message=>toasts.push(message);globalThis.api={state,pages,actions,bindPage,addFiles,removeAttachment,clearEmailAttachments,summaryRequest,summaryInputIssue,composedAttachmentCharacters,canSummarize,fileList,persist,closeAttachmentDraft,recordForm,normalizedAttachment,refreshConnection,runSummary,validateImport};',context);
  const a=context.api;
  Object.assign(a.state,{route:'email',attachmentsSupported:true,dbChecked:true,dbConfigured:true,dbReady:true,dbCategorySupport:true,dbEvaluationSupport:true,connection:'ready',models:[{id:'model'}],liveModel:'model'});
  return {a,calls,writes,toasts,elements,form};
}

test('real upload sends a file as multipart, preserves Unicode, and summary references only its ready ID',async()=>{
  const {a,calls,writes}=setup(async()=>answer({attachment:attachment()}));
  const pending=a.addFiles([file()]);assert.equal(a.state.files[0].status,'uploading');assert.equal(a.canSummarize(),false);
  await pending;
  assert.equal(calls[0].path,'/api/attachments');assert.equal(calls[0].options.headers,undefined);assert.equal(calls[0].options.body.get('file').originalFile,true);
  assert.equal(a.state.files[0].status,'ready');assert.equal(a.state.files[0].text,attachment().text);assert.equal(a.state.files[0].file,undefined);
  assert.equal(a.canSummarize(),true);assert.deepEqual(JSON.parse(JSON.stringify(a.summaryRequest())).attachments,[id]);
  assert.equal(a.summaryRequest().email.body,'');a.persist();assert.doesNotMatch(writes.at(-1),/originalFile|표 안의 한글/);
});

test('removing a pending upload ignores its late response and keeps it out of the summary',async()=>{
  let resolve;const {a}=setup(()=>new Promise(r=>resolve=r));
  const pending=a.addFiles([file()]),localId=a.state.files[0].localId,signal=a.state.files[0].controller.signal;
  a.removeAttachment('email',localId);assert.equal(signal.aborted,true);resolve(answer({attachment:attachment()}));await pending;
  assert.equal(a.state.files.length,0);assert.equal(a.canSummarize(),false);assert.equal(a.summaryRequest().attachments.length,0);
});

test('clearing input prevents all old queued files from reappearing or uploading',async()=>{
  let resolve;const {a,calls}=setup(()=>new Promise(r=>resolve=r));
  const pending=a.addFiles([file(),file('다음.pdf',456)]);assert.equal(a.state.files.length,2);
  a.clearEmailAttachments();resolve(answer({attachment:attachment()}));await pending;
  assert.equal(calls.length,1);assert.equal(a.state.files.length,0);
});

test('failed extraction is explicit and blocks even body-only execution until the failed file is removed',async()=>{
  const {a}=setup(async()=>answer({error:{code:'ENCRYPTED_DOCUMENT',message:'암호를 해제해 주세요.'}},422));
  a.state.email='정상 본문';await a.addFiles([file()]);assert.equal(a.state.files[0].status,'error');assert.equal(a.canSummarize(),false);
  assert.match(a.fileList(),/암호를 해제/);a.removeAttachment('email',a.state.files[0].localId);assert.equal(a.canSummarize(),true);
});

test('legacy extensions and oversized or duplicate files never make upload requests',async()=>{
  const {a,calls,toasts}=setup(async()=>answer({attachment:attachment()}));
  await a.addFiles([file('old.doc'),file('old.ppt'),file('bad.exe'),file('large.hwp',10*1024*1024+1),file('empty.txt',0)]);
  assert.equal(calls.length,0);assert.match(toasts.at(-1),/DOCX·PPTX/);
  await a.addFiles([file(),file()]);assert.equal(calls.length,1);assert.equal(a.state.files.length,1);
});

test('file previews escape source text and names while keeping actual extracted text unchanged',async()=>{
  const value=attachment({filename:'<img src=x>.txt',text:'<script>attack</script>\u200b',textChars:Array.from('<script>attack</script>\u200b').length,warnings:['<svg onload=alert(1)>']});
  const {a}=setup(async()=>answer({attachment:value}));await a.addFiles([file('test.txt')]);
  const html=a.fileList();assert.doesNotMatch(html,/<script>|<img src=x>|<svg onload=/);assert.match(html,/&lt;script&gt;/);assert.equal(a.state.files[0].text,value.text);
});

test('text limits and stale backend capabilities block spending model tokens',()=>{
  const {a}=setup();a.state.email='a'.repeat(30000);a.state.files=[attachment({textChars:30001})];
  assert.equal(a.canSummarize(),false);assert.match(a.summaryInputIssue(),/60,000/);
  a.state.email='본문';a.state.attachmentsSupported=false;assert.equal(a.canSummarize(),false);assert.match(a.summaryInputIssue(),/서버 연결/);
});

test('only ready referenced indirect attachments become executable and direct-only selection never includes them',()=>{
  const base={id:'attached',categoryId:'attachment-injection',vector:'indirect',channel:'attachment',enabled:true,attachmentIds:[id],attachments:[attachment()]};
  const rows=[base,{...base,id:'missing',attachmentIds:[secondId]},{...base,id:'wrong-path',vector:'direct'},{...base,id:'not-ready',attachments:[attachment({status:'processing'})]}];
  const groups=categoryTools.categoryGroups(categoryTools.categorySeeds,rows);assert.equal(groups.find(g=>g.id==='attachment-injection').runnable,1);assert.equal(groups.find(g=>g.id==='attachment-injection').unsupported,3);
  const direct=categoryTools.vectorSelectionPlan(rows,['direct'],['direct:attachment-injection']);assert.equal(direct.calls,0);
  const indirect=categoryTools.vectorSelectionPlan(rows,['indirect'],['indirect:attachment-injection']);assert.deepEqual(indirect.runnable.map(r=>r.id),['attached']);
});

test('attachment-only scenario save sends IDs without file bytes/text and retains exact payload whitespace',async()=>{
  let saved;
  const {a,form}=setup(async(path,options)=>{
    saved=JSON.parse(options.body);
    return answer({normal:seeds.normalSeeds,attacks:[{...saved,attachments:[attachment()],dbId:'saved',dbVersion:1}],categories:categoryTools.categorySeeds,evaluationModes:['manual','exact','system_prompt_leak']});
  });
  a.state.tab='attacks';a.recordForm();a.state.attachmentDraft.files=[{...attachment(),localId:'local'}];
  form.values.payload=' \n\u200b ';await form.listeners.submit({preventDefault(){},target:form});
  assert.deepEqual(saved.attachmentIds,[id]);assert.equal(saved.payload,' \n\u200b ');assert.equal(saved.attachments,undefined);assert.equal(a.state.attachmentDraft,null);
  a.recordForm(a.state.attacks[0].id);assert.equal(a.state.attachmentDraft.files[0].id,id);
});

test('closing a scenario draft invalidates pending upload completion',async()=>{
  let resolve;const {a}=setup(()=>new Promise(r=>resolve=r));a.state.tab='attacks';a.recordForm();
  const old=a.state.attachmentDraft;const pending=a.addFiles([file()],'scenario');a.closeAttachmentDraft();
  resolve(answer({attachment:attachment()}));await pending;assert.equal(a.state.attachmentDraft,null);assert.equal(old.files[0].status,'uploading');
});

test('health reports attachment availability independently of whether a model key exists',async()=>{
  const {a}=setup(async path=>answer(path==='/api/health'?{configured:false,features:{attachments:true},database:{configured:true,ready:true}}:{normal:[],attacks:[],categories:categoryTools.categorySeeds}));
  await a.refreshConnection();assert.equal(a.state.connection,'missing');assert.equal(a.state.attachmentsSupported,true);assert.equal(a.state.dbReady,true);
});

test('the five-file cap and one upload batch prevent concurrent extraction bursts',async()=>{
  let resolve;const {a,calls,toasts}=setup(()=>new Promise(r=>resolve=r));
  const pending=a.addFiles([file()]);await a.addFiles([file('extra.pdf',200)]);
  assert.equal(calls.length,1);assert.match(toasts.at(-1),/끝난 뒤/);
  resolve(answer({attachment:attachment()}));await pending;
  a.state.files=Array.from({length:5},(_,i)=>attachment({id:'existing-'+i,filename:i+'.txt'}));
  await a.addFiles([file('sixth.pdf',200)]);assert.equal(calls.length,1);assert.match(toasts.at(-1),/최대 5개/);
});

test('backup validation rejects malformed attachment metadata instead of crashing when opened',()=>{
  const {a}=setup();
  for(const files of [{bad:true},[null],[attachment({warnings:'not-a-list'})]]){
    const row={...seeds.attackSeeds[0],id:'attachment-backup',vector:'indirect',channel:'attachment',payload:'',attachmentIds:[id],attachments:files};
    assert.throws(()=>a.validateImport({schemaVersion:1,normal:seeds.normalSeeds,attacks:[row],plans:[],notes:[]}));
  }
  const row={...seeds.attackSeeds[0],id:'attachment-backup',vector:'indirect',channel:'attachment',payload:'',attachmentIds:[id],attachments:[attachment()]};
  assert.doesNotThrow(()=>a.validateImport({schemaVersion:1,normal:seeds.normalSeeds,attacks:[row],plans:[],notes:[]}));
});

test('summary limits match the composed Unicode input including title, quoted filename and delimiters',()=>{
  const {a}=setup();a.state.emailTitle='제목 😀';a.state.email='😀'.repeat(10000);
  const filename='문서 "😀"\\이름.txt',empty=attachment({filename,text:'',textChars:0});
  const fixed=a.composedAttachmentCharacters(`제목: ${a.state.emailTitle}\n\n${a.state.email}`,[empty]);
  for(const target of [59999,60000,60001]){
    const text='가'.repeat(target-fixed);a.state.files=[attachment({filename,text,textChars:text.length})];
    const actual=`제목: ${a.state.emailTitle}\n\n${a.state.email}\n\n[첨부파일 1: ${JSON.stringify(filename)}]\n${text}\n[첨부파일 1 끝]`;
    assert.equal(Array.from(actual).length,target);assert.equal(a.composedAttachmentCharacters(`제목: ${a.state.emailTitle}\n\n${a.state.email}`,a.state.files),target);
    assert.equal(a.canSummarize(),target<=60000);
  }
  a.state.files=[];a.state.email='😀'.repeat(30000);assert.equal(a.canSummarize(),true);
  a.state.email+='😀';assert.match(a.summaryInputIssue(),/30,000/);
});

test('email body input and counter preserve astral text and reject over-limit content without truncating',()=>{
  const {a,elements}=setup();
  for(const selector of ['#email-body','#email-title','#live-model','#sample','#files','#dropzone']){
    elements[selector]={listeners:{},addEventListener(type,handler){this.listeners[type]=handler;}};
  }
  elements['#char-count']={};elements['#summarize-button']={};elements['#attachment-input-status']={};
  a.bindPage();
  const input=value=>elements['#email-body'].listeners.input({target:{value}});

  input('한글 😀\u200b');
  assert.equal(elements['#char-count'].textContent,'5자');
  assert.match(a.pages.email(),/id="char-count"[^>]*>5자<\/span>/);
  // HTML maxlength counts UTF-16 units, so the codepoint validator owns this limit.
  assert.doesNotMatch(a.pages.email().match(/<textarea[^>]*id="email-body"[^>]*>/)[0],/maxlength=/);

  for(const character of ['가','😀']){
    const boundary=character.repeat(30000);input(boundary);
    assert.equal(a.state.email,boundary);assert.equal(a.summaryRequest().email.body,boundary);
    assert.equal(elements['#char-count'].textContent,(30000).toLocaleString()+'자');
    assert.equal(elements['#summarize-button'].disabled,false);

    const overflow=boundary+character;input(overflow);
    assert.equal(a.state.email,overflow);assert.equal(a.summaryRequest().email.body,overflow);
    assert.equal(elements['#char-count'].textContent,(30001).toLocaleString()+'자');
    assert.equal(elements['#summarize-button'].disabled,true);
    assert.match(elements['#attachment-input-status'].textContent,/30,000/);
  }
});

test('scenario limits include only its payload and attachment wrappers, at the same Unicode boundary',async()=>{
  for(const target of [59999,60000,60001]){
    let saved;
    const {a,form,elements}=setup(async(path,options)=>{
      saved=JSON.parse(options.body);return answer({normal:[],attacks:[],categories:categoryTools.categorySeeds,evaluationModes:['manual','exact','system_prompt_leak']});
    });
    a.state.tab='attacks';a.recordForm();form.values.payload='😀'.repeat(10000);
    const filename='기관 "😀".hwp',empty=attachment({filename,text:'',textChars:0});
    const fixed=a.composedAttachmentCharacters(form.values.payload,[empty]);const text='나'.repeat(target-fixed);
    a.state.attachmentDraft.files=[attachment({filename,text,textChars:text.length})];
    await form.listeners.submit({preventDefault(){},target:form});
    assert.equal(Boolean(saved),target<=60000);
    if(target>60000)assert.match(elements['#form-error'].textContent,/60,000/);
  }
});
