// Read-only dashboard requests stay independent of experiment execution and polling.
export function createDashboardController({fetchImpl=fetch,onChange=()=>{},preview=false,now=Date.now}={}) {
  const state={data:null,loading:false,error:'',credits:null,creditsLoading:false,creditsError:''};
  let pending=null,lastLoaded=null;
  async function read(path) {
    const controller=new AbortController();
    const timer=setTimeout(()=>controller.abort(),20000);
    try {
      const response=await fetchImpl(path,{method:'GET',credentials:'same-origin',cache:'no-store',signal:controller.signal});
      const result=await response.json();
      if(!response.ok)throw new Error(result?.error?.message||'서버에서 현황을 불러오지 못했어요. 잠시 후 다시 확인해 주세요.');
      return result;
    } catch(error) {
      if(error?.name==='AbortError')throw new Error('현황 조회 시간이 초과됐어요. 새로고침으로 다시 확인해 주세요.');
      if(error instanceof SyntaxError)throw new Error('서버 응답을 확인하지 못했어요. 잠시 후 다시 확인해 주세요.');
      throw error;
    } finally {clearTimeout(timer);}
  }
  function load(force=false) {
    if(preview){state.error='UI 검토 모드에서는 실제 DB 현황을 조회하지 않아요.';state.creditsError='실제 서버에 연결하면 잔여 크레딧을 확인할 수 있어요.';onChange();return Promise.resolve();}
    if(pending)return pending;
    if(!force&&lastLoaded!==null&&now()-lastLoaded<60000)return Promise.resolve();
    state.loading=true;state.creditsLoading=true;state.error='';state.creditsError='';onChange();
    pending=Promise.allSettled([
      read('/api/dashboard').then(data=>{state.data=data;}).catch(error=>{state.data=null;state.error=error.message||'DB 현황을 불러오지 못했어요.';}).finally(()=>{state.loading=false;onChange();}),
      read('/api/credits').then(credits=>{state.credits=credits;}).catch(error=>{state.credits=null;state.creditsError=error.message||'크레딧 잔액을 확인하지 못했어요.';}).finally(()=>{state.creditsLoading=false;onChange();}),
    ]).finally(()=>{lastLoaded=now();pending=null;});
    return pending;
  }
  return {state,load};
}
