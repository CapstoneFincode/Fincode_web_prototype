"""HTTP persistence boundary and a durable, bounded native experiment worker."""
import asyncio
import logging
import time
import psycopg
from fastapi import Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from backend.database import Database, DataError

log = logging.getLogger(__name__)


class Persistence:
    def __init__(self, url, config):
        self.db = Database(url) if url else None
        self.config = config

    async def call(self, method, *args):
        if not self.db:
            raise DataError('DATABASE_URL 설정 및 DB 초기화가 필요합니다.',503,'DATABASE_NOT_CONFIGURED')
        try:
            return await asyncio.to_thread(getattr(self.db,method), *args)
        except psycopg.Error:
            raise DataError('DB 연결 또는 저장에 실패했습니다. 서버의 DB 상태를 확인해 주세요.',503,'DATABASE_UNAVAILABLE') from None

    async def health(self):
        if not self.db:
            return {'configured':False,'ready':False}
        try:
            return await self.call('health')
        except DataError:
            return {'configured':True,'ready':False}

    async def execute(self, run, generate):
        start=time.perf_counter()
        try:
            payload=await generate(run['model'],run['messages'],run.get('maxTokens',self.config['maxTokens']))
            status,code='succeeded',200
        except Exception as error:
            # APIError is owned by app.py; avoid circular imports and never save a raw exception/key.
            if hasattr(error,'payload') and hasattr(error,'status'):
                payload,code=error.payload,error.status
            else:
                payload,code={'error':{'code':'EXECUTION_FAILED','message':'실행 처리 중 오류가 발생했습니다.'}},500
            status='timed_out' if code==504 else 'failed'
        payload={**payload,'runId':run['runId'],'httpStatus':code}
        try:
            saved=await self.call('finish',run['runId'],payload,status,max(0,round((time.perf_counter()-start)*1000)))
        except DataError as error:
            error.observed_result=payload
            raise
        if not saved:
            raise DataError('실행 기록이 이미 종료되었습니다. 실험 결과를 확인해 주세요.',409,'RUN_ALREADY_CLOSED')
        return payload,code

    async def summarize(self,data,messages,generate):
        run=await self.call('prepare_summary',data,self.config)
        if run['existing']:
            if not run['response']:
                raise DataError('동일한 요청이 실행 중이거나 중단되었습니다. 실험 기록을 확인해 주세요.',409,'REQUEST_ALREADY_SUBMITTED')
            result=run['response']
        else:
            try:
                result,_=await self.execute({'runId':run['runId'],'model':data['model'],'messages':messages},generate)
            except DataError as error:
                # The call may have consumed tokens. Do not automatically repeat it.
                return JSONResponse({**getattr(error,'observed_result',{}),'httpStatus':503,'error':{'code':'RESULT_SAVE_FAILED','message':error.message+' 모델 요청은 이미 전송되었을 수 있으므로 자동 재시도하지 않습니다.'},'runId':run['runId'],'experimentId':run['id'],'database':{'saved':False}},status_code=503)
        return JSONResponse(jsonable_encoder({**result,'experimentId':run['id'],'database':{'saved':True}}),status_code=result['httpStatus'])

    async def worker_once(self,generate,key):
        if not key():
            return False
        run=await self.call('claim_next',self.config)
        if not run:
            return False
        await self.execute(run,generate)
        return True

    async def loop(self,generate,key):
        while True:
            try:
                if await self.worker_once(generate,key):
                    continue
            except Exception:
                # No input, DSN, API key or upstream response in logs.
                log.warning('Experiment worker paused after a persistence error; no automatic call retry.')
            await asyncio.sleep(2)

    def install(self,app,read_payload,key):
        @app.exception_handler(DataError)
        async def handle_data_error(request,error):
            return JSONResponse({'error':{'code':error.code,'message':error.message}},status_code=error.status)

        @app.get('/api/data')
        async def datasets():
            return await self.call('datasets')

        @app.post('/api/data/{kind}')
        async def create(kind:str,request:Request):
            return await self.call('save',kind,await read_payload(request))

        @app.put('/api/data/{kind}/{identifier}')
        async def update(kind:str,identifier:str,request:Request):
            return await self.call('save',kind,await read_payload(request),identifier)

        @app.delete('/api/data/{kind}/{identifier}')
        async def archive(kind:str,identifier:str,request:Request):
            data=await read_payload(request)
            return await self.call('archive',kind,identifier,data.get('dbVersion'))

        @app.get('/api/experiments')
        async def experiments():
            return {'experiments':await self.call('experiments')}

        @app.post('/api/experiments',status_code=202)
        async def enqueue(request:Request):
            data=await read_payload(request)
            if not key():
                raise DataError('서버에 BAZE_API_KEY를 설정해 주세요.',503,'API_KEY_MISSING')
            return await self.call('enqueue',data,self.config)

        @app.get('/api/experiments/{identifier}')
        async def detail(identifier:str):
            return await self.call('detail',identifier)

        @app.post('/api/runs/{identifier}/evaluations')
        async def evaluate(identifier:str,request:Request):
            return await self.call('evaluate',identifier,await read_payload(request))
