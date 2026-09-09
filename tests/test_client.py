import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch

from linghub_client import LingHubClient,ClientError,process_jsonl


class ClientBatchTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.root=Path(temp.name)
        self.input=self.root/'input.jsonl';self.output=self.root/'export'
        self.records=[{'id':str(i),'lang':'sl','text':'otrok'+str(i),'task':'analyze','engine':'default'} for i in range(4)]
        self.write()
        self.client=Mock();self.client.request.return_value={'identity':'release1'}
        self.client.analyze.side_effect=lambda lang,text,engine:{'status':'ok','text':text,'provenance':{'model':'fixture'}}

    def write(self):self.input.write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in self.records))

    def test_interruption_resume_and_stable_no_duplicate_order(self):
        calls=0
        def interrupt(lang,text,engine):
            nonlocal calls
            calls+=1
            if calls==3:raise KeyboardInterrupt()
            return {'status':'ok','text':text}
        self.client.analyze.side_effect=interrupt
        self.assertRaises(KeyboardInterrupt,process_jsonl,self.client,self.input,self.output)
        manifest=json.loads((self.output/'progress.json').read_text());self.assertEqual(len(manifest['completed']),2)
        self.client.analyze.side_effect=lambda *args:{'status':'ok'}
        done=process_jsonl(self.client,self.input,self.output,resume=True)
        self.assertEqual(done['reused'],2);self.assertEqual(done['processed'],2)
        output=[json.loads(x) for x in (self.output/'results.jsonl').read_text().splitlines()]
        self.assertEqual([x['id'] for x in output],['0','1','2','3'])
        again=process_jsonl(self.client,self.input,self.output,resume=True)
        self.assertEqual(again['reused'],4);self.assertEqual(again['processed'],0)

    def test_changed_input_engine_and_resource_identity_are_recognized(self):
        process_jsonl(self.client,self.input,self.output)
        self.records[1]['text']='changed';self.records[2]['engine']='classla';self.write()
        updated=process_jsonl(self.client,self.input,self.output,resume=True)
        self.assertEqual(updated['reused'],2);self.assertTrue(updated['previous_input_changed'])
        self.client.request.return_value={'identity':'release2'}
        newer=process_jsonl(self.client,self.input,self.output,resume=True)
        self.assertEqual(newer['reused'],0);self.assertTrue(newer['previous_identity_changed'])

    def test_errors_have_stable_document_ids_and_batch_failure(self):
        self.client.analyze.side_effect=ClientError('HTTP503',503)
        result=process_jsonl(self.client,self.input,self.output)
        self.assertEqual(result['status'],'complete_with_errors');self.assertEqual(result['failed_ids'],['0','1','2','3'])

    def test_exports_private_progress_has_no_raw_text(self):
        process_jsonl(self.client,self.input,self.output)
        self.assertEqual(self.output.stat().st_mode&0o777,0o700)
        self.assertEqual((self.output/'progress.json').stat().st_mode&0o777,0o600)
        self.assertEqual((self.output/'results.jsonl').stat().st_mode&0o777,0o600)
        self.assertNotIn('otrok', (self.output/'progress.json').read_text())

    def test_shared_output_and_symlinks_rejected(self):
        self.output.mkdir(mode=0o755)
        self.assertRaises(ValueError,process_jsonl,self.client,self.input,self.output)
        link=self.root/'link';link.symlink_to(self.output)
        self.assertRaises(ValueError,process_jsonl,self.client,self.input,link)

    def test_duplicate_ids_and_oversized_docs_are_not_silently_accepted(self):
        self.records[1]['id']='0';self.write()
        self.assertRaises(ValueError,process_jsonl,self.client,self.input,self.output)
        self.records[1]['id']='1';self.records[0]['text']='a'*10001;self.write()
        result=process_jsonl(self.client,self.input,self.output)
        self.assertEqual(result['failed_ids'],['0'])

    def test_corrupt_completed_result_is_rejected(self):
        done=process_jsonl(self.client,self.input,self.output)
        slot=self.output/'items'/(done['completed']['0']['key']+'.json');slot.write_text('{}')
        self.assertRaisesRegex(ValueError,'checksum mismatch',process_jsonl,self.client,self.input,self.output,True)

    def test_client_refuses_nonlocal_or_credentialed_endpoint(self):
        for url in ('http://example.com:80','http://u:p@127.0.0.1:8010','http://127.0.0.1:8010/path'):
            self.assertRaises(ValueError,LingHubClient,url)


if __name__=='__main__':unittest.main()
