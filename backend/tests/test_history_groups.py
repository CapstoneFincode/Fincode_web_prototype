"""운영 데이터 쓰기 없이 공격·방어 목록 조회 조건을 검증한다."""
import unittest
from contextlib import contextmanager
from backend.results import experiment_page
from backend.database import DataError

class Rows:
 def __init__(self,rows):self.rows=rows
 def fetchone(self):return self.rows[0]
 def fetchall(self):return self.rows
class Connection:
 def __init__(self):self.queries=[]
 def execute(self,query,params=()):
  self.queries.append((query,params))
  return Rows([{'count':0}]) if 'count(*)::int AS count' in query else Rows([])
class DB:
 def __init__(self):self.connection=Connection()
 @contextmanager
 def connect(self):yield self.connection
class HistoryGroupTest(unittest.TestCase):
 def test_attack_and_defense_are_filtered_before_count_and_pagination(self):
  for group,fragment in [('attack',"AND e.purpose='attack' AND NOT EXISTS"),('defense',"AND e.purpose='attack' AND EXISTS")]:
   db=DB();result=experiment_page(db,experiment_group=group)
   self.assertEqual(result['total'],0)
   queries=[q for q,p in db.connection.queries if 'experiment_cases gc' in q]
   self.assertEqual(len(queries),2)
   self.assertTrue(all(fragment in q for q in queries))
   self.assertTrue(all('gr.security_gateway_enabled' in q for q in queries))
   self.assertTrue(any('bool_or(r.security_gateway_enabled) AS defense_enabled' in q for q,p in db.connection.queries))
 def test_default_history_keeps_all_records_and_uses_only_reads(self):
  db=DB();experiment_page(db)
  for q,p in db.connection.queries:
   self.assertNotIn('experiment_cases gc',q)
   self.assertTrue(q.lstrip().startswith(('SET TRANSACTION','SELECT','WITH selected')))
 def test_invalid_group_is_rejected_without_query(self):
  db=DB()
  with self.assertRaises(DataError):experiment_page(db,experiment_group='invalid')
  self.assertEqual(db.connection.queries,[])
