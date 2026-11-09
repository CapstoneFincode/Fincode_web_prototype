"""Approval integration tests; FINCODE_TEST_DATABASE_URL must be a disposable DB.

Uses real PostgreSQL transactions and a mock upstream that must never be called.
"""
import os
import unittest
from uuid import uuid4

import httpx

from backend.app import create_app
from backend.database import Database, digest, insert
from backend.migrate import migrate


URL=os.environ.get('FINCODE_TEST_DATABASE_URL')
NORMAL={'title':'합성 이메일 승인 테스트','body':'11월 10일 오후 2시 회의실 A에서 회의합니다.',
        'reference':'11월 10일 오후 2시 회의실 A에서 회의 예정.',
        'requiredFacts':['11월 10일','오후 2시','회의실 A'],'reviewStatus':'pending'}
NOTE='사용자가 합성 정상 이메일과 기준 정답의 평가용 승인을 요청함. 독립적인 사람 검수 결과는 아님.'


@unittest.skipUnless(URL,'Set FINCODE_TEST_DATABASE_URL to a disposable PostgreSQL DB')
class DatasetReviewTest(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        migrate(URL)
        cls.db=Database(URL,actor='review-test-actor')
        cls.db.seed()

    async def asyncSetUp(self):
        self.calls=[]
        def upstream(request):
            self.calls.append(request)
            raise AssertionError('Dataset approval must not call a model')
        self.app=create_app(database_url=URL,api_key='test-key-not-real',run_worker=False,
                            upstream_transport=httpx.MockTransport(upstream))
        self.app.state.database.actor='review-test-actor'
        self.client=httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),base_url='http://testserver')
        self.rows=[self.make_normal(),self.make_normal()]

    async def asyncTearDown(self):
        await self.client.aclose()
        self.assertEqual(self.calls,[])

    def make_normal(self,**changes):
        key='REVIEW-'+str(uuid4())
        with self.db.connect() as conn:
            self.db._save(conn,'normal',{**NORMAL,**changes},key)
        return self.dataset_row(key)

    def dataset_row(self,key):
        return next(row for row in self.db.datasets()['normal'] if row['id']==key)

    @staticmethod
    def item(row):
        return {'id':row['id'],'dbVersion':row['dbVersion'],'groundTruthId':row['groundTruthId'],
                'groundTruthVersion':row['groundTruthVersion'],'contentSha256':row['reviewContentSha256']}

    async def review(self,rows=None,items=None,note=NOTE):
        return await self.client.post('/api/data/normal/review',json={
            'items':items if items is not None else [self.item(row) for row in (self.rows if rows is None else rows)],
            'note':note})

    def snapshot(self,row):
        with self.db.connect() as conn:
            email=conn.execute('SELECT * FROM email_cases WHERE id=%s',(row['dbId'],)).fetchone()
            refs=conn.execute('SELECT * FROM ground_truths WHERE email_case_id=%s ORDER BY revision',(row['dbId'],)).fetchall()
            counts=conn.execute('''SELECT (SELECT count(*) FROM email_cases) AS emails,
                (SELECT count(*) FROM ground_truths) AS references,
                (SELECT count(*) FROM experiments) AS experiments,
                (SELECT count(*) FROM runs) AS runs,(SELECT count(*) FROM evaluations) AS evaluations''').fetchone()
        return email,refs,counts

    async def test_api_exposes_exact_review_versions_and_canonical_content_hash(self):
        response=await self.client.get('/api/data')
        self.assertEqual(response.status_code,200,response.text)
        row=next(row for row in response.json()['normal'] if row['id']==self.rows[0]['id'])
        self.assertEqual(row['groundTruthVersion'],1)
        self.assertEqual(row['referenceReviewStatus'],'draft')
        self.assertEqual(row['referenceReviewNote'],'')
        self.assertEqual(row['reviewContentSha256'],digest({key:row[key] for key in ('title','body','reference','requiredFacts')}))
        email,refs,_=self.snapshot(row)
        self.assertEqual(row['groundTruthId'],str(refs[0]['id']))
        self.assertEqual(row['dbVersion'],email['revision'])

    async def test_batch_changes_only_review_metadata_and_is_idempotent(self):
        before=[self.snapshot(row) for row in self.rows]
        result=await self.review()
        self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(result.json(),{'reviewedCount':2,'alreadyReviewedCount':0,'ids':[row['id'] for row in self.rows]})
        after=[self.snapshot(row) for row in self.rows]
        for (old_email,old_refs,old_counts),(email,refs,counts),row in zip(before,after,self.rows):
            self.assertEqual(counts,old_counts)
            self.assertEqual(email['label_review_status'],'reviewed')
            self.assertEqual(email['label_reviewed_by'],'review-test-actor')
            self.assertIsNotNone(email['label_reviewed_at'])
            self.assertEqual(refs[0]['review_status'],'reviewed')
            self.assertEqual(refs[0]['reviewed_by'],'review-test-actor')
            self.assertEqual(refs[0]['reviewed_at'],email['label_reviewed_at'])
            self.assertEqual(refs[0]['review_note'],'사용자 요청에 따른 승인 처리: '+NOTE)
            email_fields={'label_review_status','label_reviewed_by','label_reviewed_at'}
            reference_fields={'review_status','reviewed_by','reviewed_at','review_note'}
            self.assertEqual({k:v for k,v in email.items() if k not in email_fields},
                             {k:v for k,v in old_email.items() if k not in email_fields})
            self.assertEqual([{k:v for k,v in r.items() if k not in reference_fields} for r in refs],
                             [{k:v for k,v in r.items() if k not in reference_fields} for r in old_refs])
            self.assertEqual(self.dataset_row(row['id'])['reviewContentSha256'],row['reviewContentSha256'])
        again=await self.review(note='다른 재요청 메모')
        self.assertEqual(again.status_code,200,again.text)
        self.assertEqual(again.json()['alreadyReviewedCount'],2)
        self.assertEqual(again.json()['reviewedCount'],0)
        self.assertEqual([self.snapshot(row) for row in self.rows],after)

    async def test_conflicts_abort_every_item_before_any_approval(self):
        before=[self.snapshot(row) for row in self.rows]
        invalid=[{'dbVersion':2},{'groundTruthVersion':2},{'groundTruthId':str(uuid4())},
                 {'contentSha256':'0'*64},{'id':'MISSING-'+str(uuid4())}]
        for changes in invalid:
            with self.subTest(changes=changes):
                result=await self.review(items=[self.item(self.rows[0]),{**self.item(self.rows[1]),**changes}])
                self.assertEqual(result.status_code,409,result.text)
                self.assertEqual(result.json()['error']['code'],'REVIEW_CONFLICT')
                self.assertEqual([self.snapshot(row) for row in self.rows],before)

    async def test_only_latest_email_and_reference_revisions_can_be_approved(self):
        row=self.rows[0]
        changed=await self.client.put('/api/data/normal/'+row['id'],json={**row,'body':'수정된 이메일 본문'})
        self.assertEqual(changed.status_code,200,changed.text)
        stale=await self.review(rows=[row])
        self.assertEqual(stale.status_code,409,stale.text)
        latest=self.dataset_row(row['id'])
        self.assertEqual(latest['reviewStatus'],'pending')
        with self.db.connect() as conn:
            insert(conn,'ground_truths',{'id':str(uuid4()),'email_case_id':latest['dbId'],'revision':2,
                'reference_summary':'새 기준 요약','required_facts':['새 핵심 정보'],
                'review_status':'draft','created_by':'review-test-actor'})
        stale=await self.review(rows=[latest])
        self.assertEqual(stale.status_code,409,stale.text)
        current=self.dataset_row(row['id'])
        approved=await self.review(rows=[current])
        self.assertEqual(approved.status_code,200,approved.text)
        with self.db.connect() as conn:
            old=conn.execute('SELECT label_review_status FROM email_cases WHERE id=%s',(row['dbId'],)).fetchone()
            old_ref=conn.execute('SELECT review_status FROM ground_truths WHERE id=%s',(latest['groundTruthId'],)).fetchone()
        self.assertEqual(old['label_review_status'],'pending')
        self.assertEqual(old_ref['review_status'],'draft')

    async def test_sample_archived_non_normal_and_missing_reference_are_rejected(self):
        sample=next(row for row in self.db.datasets()['normal'] if row['provenance']=='sample' and row['groundTruthId'])
        missing=self.make_normal(reference='')
        unknown=self.make_normal()
        archived=self.make_normal()
        with self.db.connect() as conn:
            conn.execute("UPDATE email_cases SET expected_label='unknown' WHERE id=%s",(unknown['dbId'],))
            conn.execute('UPDATE email_cases SET archived_at=now() WHERE id=%s',(archived['dbId'],))
        before=self.snapshot(self.rows[0])
        for row,expected in [(sample,400),(missing,400),(unknown,400),(archived,409)]:
            with self.subTest(kind=row['id']):
                item=self.item(row)
                if row is missing:
                    item.update(groundTruthId=str(uuid4()),groundTruthVersion=1)
                result=await self.review(items=[self.item(self.rows[0]),item])
                self.assertEqual(result.status_code,expected,result.text)
                self.assertEqual(self.snapshot(self.rows[0]),before)

    async def test_partial_previous_approval_preserves_its_audit_fields(self):
        label_reviewed=self.make_normal(reviewStatus='reviewed')
        before=self.snapshot(label_reviewed)[0]
        result=await self.review(rows=[label_reviewed])
        self.assertEqual(result.status_code,200,result.text)
        email=self.snapshot(label_reviewed)[0]
        for field in ('label_reviewed_by','label_reviewed_at'):
            self.assertEqual(email[field],before[field])
        row=self.rows[0]
        with self.db.connect() as conn:
            conn.execute("UPDATE ground_truths SET review_status='reviewed',reviewed_by='prior-reviewer',reviewed_at=now()-interval '1 day',review_note='기존 승인 메모' WHERE id=%s",(row['groundTruthId'],))
        before_ref=self.snapshot(row)[1]
        result=await self.review(rows=[row])
        self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(self.snapshot(row)[1],before_ref)

    async def test_validation_rejects_ambiguous_or_oversized_requests(self):
        item=self.item(self.rows[0])
        bodies=[{'items':[],'note':NOTE},{'items':[item]*101,'note':NOTE},
                {'items':[item,item],'note':NOTE},{'items':[item],'note':''},
                {'items':[item],'note':NOTE,'reviewedBy':'forged-actor'},
                {'items':[{**item,'dbVersion':True}],'note':NOTE},
                {'items':[{**item,'groundTruthVersion':'1'}],'note':NOTE},
                {'items':[{**item,'groundTruthId':'invalid'}],'note':NOTE},
                {'items':[{**item,'contentSha256':'invalid'}],'note':NOTE},
                {'items':[{**item,'body':'replacement body'}],'note':NOTE}]
        before=self.snapshot(self.rows[0])
        for body in bodies:
            result=await self.client.post('/api/data/normal/review',json=body)
            self.assertEqual(result.status_code,400,result.text)
        self.assertEqual(self.snapshot(self.rows[0]),before)

    async def test_full_100_item_batch_has_one_atomic_approval_timestamp(self):
        keys=['REVIEW-'+str(uuid4()) for _ in range(100)]
        with self.db.connect() as conn:
            for key in keys:
                self.db._save(conn,'normal',NORMAL,key)
        rows={row['id']:row for row in self.db.datasets()['normal'] if row['id'] in keys}
        response=await self.review(rows=[rows[key] for key in keys])
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['reviewedCount'],100)
        with self.db.connect() as conn:
            counts=conn.execute('''SELECT count(*) AS total,count(DISTINCT e.label_reviewed_at) AS timestamps,
                bool_and(e.label_review_status='reviewed' AND g.review_status='reviewed'
                    AND e.label_reviewed_at=g.reviewed_at) AS all_reviewed
                FROM email_cases e JOIN ground_truths g ON g.email_case_id=e.id WHERE e.case_key=ANY(%s)''',(keys,)).fetchone()
        self.assertEqual(counts,{'total':100,'timestamps':1,'all_reviewed':True})
