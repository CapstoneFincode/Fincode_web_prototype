/** Pure category catalog and experiment selection rules shared by UI and tests. */
export const categorySeeds = [
  {id:'system-prompt-leak',name:'시스템 프롬프트 유출'},
  {id:'instruction-override',name:'지시 덮어쓰기'},
  {id:'role-spoofing',name:'역할·권한 사칭'},
  {id:'special-token',name:'특수 토큰 인젝션'},
  {id:'hidden-text',name:'비가시 문자 은닉'},
  {id:'attachment-injection',name:'첨부 콘텐츠 주입'},
  {id:'uncategorized',name:'미분류'},
];
export const categoryNameKey = value => value.normalize('NFKC').trim().replace(/\s+/gu,' ').toLocaleLowerCase('ko');
export const categoryFor = attack => attack.categoryId || 'uncategorized';
export function normalizeCategoryData(categories, attacks){
  const catalog = categories === undefined ? structuredClone(categorySeeds) : structuredClone(categories);
  if(!Array.isArray(catalog)||catalog.length>1000)throw Error('카테고리 목록 형식을 확인해 주세요.');
  const ids=new Set(),names=new Set();
  for(const item of catalog){
    if(!item||typeof item.id!=='string'||!item.id||item.id.length>80||typeof item.name!=='string'||!item.name.trim()||item.name.length>60)throw Error('카테고리 형식을 확인해 주세요.');
    const normalized=item.name.normalize('NFKC').trim().replace(/\s+/gu,' ');
    if(!normalized||normalized.length>60||/\p{C}/u.test(normalized))throw Error('카테고리 이름은 표시 가능한 1~60자여야 해요.');
    const key=categoryNameKey(normalized);
    if(ids.has(item.id)||names.has(key))throw Error('중복된 카테고리가 있어요.');
    ids.add(item.id);names.add(key);item.name=normalized;
  }
  if(!ids.has('uncategorized')){if(catalog.length>=1000)throw Error('카테고리는 미분류를 포함해 최대 1,000개까지 지원해요.');if(names.has(categoryNameKey('미분류')))throw Error('미분류는 기본 카테고리 이름이에요.');catalog.push({id:'uncategorized',name:'미분류'});ids.add('uncategorized');}
  const rows=attacks.map(item=>{
    const categoryId=categoryFor(item);
    if(!ids.has(categoryId))throw Error('시나리오의 카테고리가 목록에 없어요. 카테고리를 포함한 백업을 사용해 주세요.');
    return {...item,categoryId};
  });
  return {categories:catalog,attacks:rows};
}
export function categoryGroups(categories, attacks){
  return categories.map(category=>{
    const rows=attacks.filter(a=>categoryFor(a)===category.id);
    return {...category,rows,total:rows.length,runnable:rows.filter(a=>a.enabled&&a.channel!=='attachment').length,
      disabled:rows.filter(a=>!a.enabled).length,unsupported:rows.filter(a=>a.enabled&&a.channel==='attachment').length};
  });
}
export function selectionPlan(attacks,categoryIds,repeat=1,{maxScenarios=10,maxCalls=100}={}){
  const chosen=new Set(categoryIds);
  const rows=attacks.filter(a=>chosen.has(categoryFor(a)));
  const runnable=rows.filter(a=>a.enabled&&a.channel!=='attachment');
  const disabled=rows.filter(a=>!a.enabled).length;
  const unsupported=rows.filter(a=>a.enabled&&a.channel==='attachment').length;
  let error='';
  if(!Number.isInteger(repeat)||repeat<1||repeat>10)error='반복 횟수는 1~10회로 입력해 주세요.';
  else if(!runnable.length)error='실행 가능한 공격이 있는 카테고리를 선택해 주세요.';
  else if(runnable.length>maxScenarios)error=`선택한 공격은 ${runnable.length}건이에요. 현재 한 실험은 최대 ${maxScenarios}건까지 지원해요. 카테고리를 나누어 선택해 주세요.`;
  else if(runnable.length*repeat>maxCalls)error=`한 실험의 호출 수는 최대 ${maxCalls}회예요. 반복 횟수를 줄여 주세요.`;
  return {rows,runnable,disabled,unsupported,calls:runnable.length*repeat,error};
}
