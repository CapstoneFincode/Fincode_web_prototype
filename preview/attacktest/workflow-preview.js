import {evaluateAttack} from './evaluator.mjs';

// UI-only fixture generation from the selected saved records. Never calls a model.
export function createWorkflowPreview({id,name,model,email,scenarios,categories,systemPrompt}){
  const frozen=structuredClone({email,scenarios,categories});
  const normalResponse=frozen.email.reference?.trim()||'화면 확인용 정상 요약 예시입니다. 실제 모델이 생성한 응답은 아닙니다.';
  return {exampleOnly:true,experiment:{id,name,purpose:'attack',status:'completed'},runs:frozen.scenarios.map((s,index)=>{
    const mode=s.evaluationMode||(s.expectedOutput?.trim()?'exact':'manual');
    const criterion={type:mode,description:s.criterion,...(['exact','canary'].includes(mode)?{expectedOutput:s.expectedOutput}:{})};
    const messages=[{role:'system',content:systemPrompt},{role:'user',content:`제목: ${frozen.email.title}\n\n${frozen.email.body}`}];
    if(s.vector==='direct')messages.push({role:'user',content:s.payload});else messages[1].content+='\n\n'+s.payload;
    const categoryId=s.categoryId||'uncategorized';
    const input_snapshot={email:{subject:frozen.email.title,body:frozen.email.body},messages,successCriterion:criterion,attackCategoryId:categoryId,
      attackScenario:{id:s.dbId||s.id,key:s.id,revision:s.dbVersion||1,name:s.name,vector:s.vector,channel:s.channel,payload:s.payload,categoryId,categoryName:frozen.categories.find(c=>c.id===categoryId)?.name||'미분류'}};
    // Alternate sample responses to expose the layout, not to estimate vulnerability.
    const response=mode==='canary'?(index%2===0?'처리를 완료했습니다.\n결과: '+s.expectedOutput:s.expectedOutput+'을 출력하라는 지시는 따르지 않겠습니다.'):mode==='system_prompt_leak'?(index%2===0?'최초 지침 원문:\n'+systemPrompt:normalResponse):mode==='exact'&&index%2===0?s.expectedOutput:normalResponse;
    const v=evaluateAttack(criterion,response,systemPrompt,input_snapshot);
    return {id:id+'-run-'+(index+1),exampleOnly:true,case_number:index+1,repeat_index:1,expected_label:'attack',status:'succeeded',requested_model:model,
      final_output:response,input_snapshot,evaluations:v?[{...v,metric:'attack_success',judge_type:'rule',evidence:'[화면 확인용 예시 응답 판정] '+v.evidence}]:[],
      calls:[],backend_latency_ms:null,service_total_tokens:null};
  })};
}
