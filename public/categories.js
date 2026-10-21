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
/** Stable original-registration order. Legacy undated rows retain their earlier array order. */
export function registrationOrder(attacks){
  return attacks.map((row,index)=>({row,index,time:typeof row.registeredAt==='string'?Date.parse(row.registeredAt):NaN}))
    .sort((a,b)=>Number.isFinite(a.time)!==Number.isFinite(b.time)?(Number.isFinite(a.time)?1:-1):Number.isFinite(a.time)?a.time-b.time||a.index-b.index:a.index-b.index)
    .map(item=>item.row);
}
export function selectionPlan(attacks,categoryIds,categoryCounts={}, {maxScenarios=100}={}){
  const chosen=[...new Set(categoryIds)];
  const seen=new Set();
  const distinct=attacks.filter((a,index)=>{const id=a.id||a.dbId||index;if(seen.has(id))return false;seen.add(id);return true;});
  const rows=distinct.filter(a=>chosen.includes(categoryFor(a)));
  const disabled=rows.filter(a=>!a.enabled).length;
  const unsupported=rows.filter(a=>a.enabled&&a.channel==='attachment').length;
  const byCategory=chosen.map(id=>{
    const eligible=registrationOrder(rows.filter(a=>categoryFor(a)===id&&a.enabled&&a.channel!=='attachment'));
    const raw=Object.hasOwn(categoryCounts,id)?categoryCounts[id]:1;
    const count=typeof raw==='number'||typeof raw==='string'&&raw.trim()!==''?Number(raw):NaN;
    let error='';
    if(!Number.isInteger(count)||count<1)error='선택할 시나리오 수는 1 이상의 정수로 입력해 주세요.';
    else if(count>eligible.length)error=`선택한 수 ${count}건이 실행 가능한 ${eligible.length}건보다 많아요.`;
    return {id,count,available:eligible.length,error,selected:error?[]:eligible.slice(0,count)};
  });
  const runnable=byCategory.flatMap(c=>c.selected);
  let error=byCategory.find(c=>c.error)?.error||'';
  if(!chosen.length)error='실험할 공격 카테고리를 선택해 주세요.';
  else if(!error&&runnable.length>maxScenarios)error=`총 ${runnable.length}건을 선택했어요. 한 실험은 최대 ${maxScenarios}개 시나리오까지 지원해요. 카테고리별 개수를 줄여 주세요.`;
  return {rows,runnable,disabled,unsupported,byCategory,calls:runnable.length,error};
}
