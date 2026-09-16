"""Synthetic packet proof. No application mount/server/store/hook/user corpus.

Every prohibited processed-directory boundary is doubled; real archive/move
end-to-end behavior is intentionally untested. Temporary fixture files remain.
"""
from __future__ import annotations
import contextlib
import hashlib
from html.parser import HTMLParser
import importlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

ROOT = Path(os.environ.get('CHATS_PROOF_SOURCE', str(Path(__file__).resolve().parents[3])))
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True
FIXTURE_PARENT = Path(os.environ['CHATS_PROOF_FIXTURES']).resolve()
FIXTURE_PARENT.mkdir(parents=True, exist_ok=True)
EFFECTS = {'scaffold_simulated':0, 'archive_simulated':0, 'archive_count_simulated':0}


def prohibited(path):
    return isinstance(path,(str,bytes,os.PathLike)) and any(
        p.lower() in ('_done','settings.json','primitives-challenge-2026-09-09.md')
        for p in os.fsdecode(path).replace('\\','/').split('/'))


def audit(event, args):
    if event in ('open','os.listdir','os.scandir','os.mkdir','os.rename','os.remove','os.rmdir'):
        for value in args[:2]:
            if prohibited(value):
                raise RuntimeError('Proof attempted a prohibited filesystem boundary')
    if event in ('socket.connect','socket.bind','subprocess.Popen'):
        raise RuntimeError('Proof attempted a network or process boundary')


sys.addaudithook(audit)
import web.chats as chats
LAZY_IMPORT = 'flask' not in sys.modules and 'web.chats.views' not in sys.modules
from web.chats import harness, history, protocol, registry, roundtrip
from flask import Flask
from web.chats import views


class DOM(HTMLParser):
    def __init__(self, text):
        super().__init__(convert_charrefs=True)
        self.tags=[]; self.attrs=[]; self.links=[]
        self.feed(text)
    def handle_starttag(self, tag, attrs):
        self.tags.append(tag); self.attrs.extend(k for k,v in attrs)
        if tag == 'a':
            self.links.extend(v for k,v in attrs if k == 'href')


@contextlib.contextmanager
def boundaries(root):
    mkdir, isdir, listdir = os.makedirs, os.path.isdir, os.listdir
    def safe_mkdir(path, *args, **kwargs):
        if prohibited(path):
            EFFECTS['scaffold_simulated'] += 1
            # The non-prohibited parent is needed by active-message operations.
            return mkdir(os.path.dirname(path), exist_ok=True)
        return mkdir(path,*args,**kwargs)
    def safe_isdir(path):
        if prohibited(path):
            EFFECTS['archive_count_simulated'] += 1
            return False
        return isdir(path)
    def safe_listdir(path):
        if prohibited(path):
            EFFECTS['archive_count_simulated'] += 1
            return []
        return listdir(path)
    def simulated_archive(message):
        EFFECTS['archive_simulated'] += 1
        dest = root/'simulated-archive'/str(EFFECTS['archive_simulated'])
        mkdir(dest,exist_ok=True)
        target = dest/Path(message.path).name
        os.replace(message.path,target)
        message.path = str(target)
        return str(target)
    with patch.object(os,'makedirs',safe_mkdir), patch.object(os.path,'isdir',safe_isdir), patch.object(os,'listdir',safe_listdir), patch.object(protocol,'mark_done',simulated_archive):
        yield


class PackageTests(unittest.TestCase):
    def setUp(self):
        self.root=Path(tempfile.mkdtemp(prefix='chats-',dir=FIXTURE_PARENT))
        self.scope=boundaries(self.root)
        self.scope.__enter__()
        self.addCleanup(self.scope.__exit__,None,None,None)
        self.transcript=self.root/'synthetic.jsonl'
        records=[{'type':'user','message':{'role':'user','content':'alpha & beta\n'+str(i)+'\n'+('line\n'*14)}} for i in range(130)]
        records.append({'type':'attachment'})
        self.transcript.write_text(''.join(json.dumps(r)+'\n' for r in records)+'{invalid\n')
        self.session=harness.HarnessSession(harness='claude-code',session_id='synthetic',transcript_path=str(self.transcript),verified=True,workdir=str(self.root))
    def registered(self, *, pointer=None, name='fixture'):
        provenance=self.session.as_body() if pointer is None else 'TRANSCRIPT: '+pointer+'\nTRANSCRIPT-VERIFIED: yes\n'
        registry.send_registration(declared_name=name,harness='fixture',base=str(self.root),workdir=str(self.root),provenance=provenance)
        return registry.accept_registrations(str(self.root))[0]
    def client(self):
        app=Flask('synthetic-chats',template_folder=str(ROOT/'web/templates'))
        app.config.update(TESTING=True)
        # A disposable in-memory routing fixture, never the runtime application.
        chats.register(app,base=str(self.root))
        return app,app.test_client()
    def test_lazy_import_and_reexport(self):
        self.assertTrue(LAZY_IMPORT)
        self.assertIs(chats.register,views.register)
        self.assertNotIn('web.app',sys.modules)
        self.assertNotIn('store',sys.modules)
    def test_two_routes_and_no_post(self):
        app,client=self.client()
        rules=[r for r in app.url_map.iter_rules() if r.rule.startswith('/chats')]
        self.assertEqual({r.rule for r in rules},{'/chats/','/chats/<agent_id>'})
        self.assertTrue(all(r.methods == {'HEAD','OPTIONS','GET'} for r in rules))
        self.assertEqual(client.get('/chats/').status_code,200)
        self.assertEqual(client.post('/chats/').status_code,405)
    def test_index_scaffold_effect_is_simulated_and_named(self):
        before=EFFECTS['scaffold_simulated']
        app,client=self.client()
        self.assertFalse((self.root/'agents').exists())
        client.get('/chats/')
        self.assertTrue((self.root/'agents').is_dir())
        self.assertGreater(EFFECTS['scaffold_simulated'],before)
    def test_rendered_disclosure_is_current(self):
        app,client=self.client()
        text=client.get('/chats/').get_data(as_text=True)
        self.assertIn('You cannot send from here.',text)
        self.assertIn('This page has no shared shell navigation.',text)
        self.assertNotIn('It is not mounted',text)
    def test_minted_id_and_restatted_pointer(self):
        one=self.registered(name='../../unsafe name')
        self.assertRegex(one.agent_id,r'^[a-z0-9][a-z0-9-]{0,63}$')
        self.assertTrue(one.transcript_verified)
        two=self.registered(pointer=str(self.root/'missing.jsonl'))
        self.assertFalse(two.transcript_verified)
    def test_forged_sender_retains_mailbox_identity(self):
        agent=self.registered()
        text=protocol.format_message(from_seat='forged-other',to='fixture',subject='fixture',kind='REPORT',needs_reply=False,body='synthetic')
        protocol.place(registry.outbox(agent.agent_id,str(self.root)),from_seat='forged-other',text=text)
        rows=list(roundtrip.OSSide(str(self.root)).collect_reports())
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0][0],agent.agent_id)
        self.assertNotEqual(rows[0][0],rows[0][1].claimed_sender)
    def test_unknown_verb_refuses_beside_known_handler_control(self):
        agent=self.registered()
        client=roundtrip.AgentClient(declared_name='fixture',harness='fixture',base=str(self.root),agent_id=agent.agent_id)
        calls=[]
        client.handle('echo',lambda body:calls.append(True) or 'synthetic report')
        side=roundtrip.OSSide(str(self.root))
        side.hand_work(agent.agent_id,verb='unknown',subject='synthetic refusal')
        client.serve_one(timeout_seconds=0)
        self.assertEqual(calls,[])
        self.assertIn('REFUSED',[m.kind for _,m in side.collect_reports()])
        side.hand_work(agent.agent_id,verb='echo',subject='synthetic control')
        client.serve_one(timeout_seconds=0)
        self.assertEqual(calls,[True])
        self.assertIn('REPORT',[m.kind for _,m in side.collect_reports()])
    def test_incomplete_message_is_not_empty(self):
        box=self.root/'incomplete';box.mkdir()
        (box/'20260910T000000Z-deadbeef-from-fixture.md').write_text('FROM: fixture\n')
        found=protocol.scan(str(box))
        self.assertEqual(len(found.skipped_incomplete),1)
        self.assertTrue(found.loud_empty)
        complete=protocol.format_message(from_seat='fixture',to='fixture',subject='control',kind='REPORT',needs_reply=False,body='synthetic')
        protocol.place(str(box),from_seat='fixture',text=complete)
        self.assertEqual(len(protocol.scan(str(box)).delivered),1)
    def test_shape_search_counts_whole_population(self):
        window=history.search(str(self.transcript),'alpha & beta',offset=50,limit=50)
        self.assertEqual(window.matched,130,'R-CHATS-SHAPE: match count must cover the whole population')
        self.assertEqual(len(window.turns),50)
        self.assertEqual((window.counts.lines,window.counts.not_rendered,window.counts.unparseable),(132,1,1))
    def test_search_pager_preserves_decoded_query(self):
        agent=self.registered();app,client=self.client()
        text=client.get('/chats/'+agent.agent_id+'?q=alpha+%26+beta').get_data(as_text=True)
        links=[p for p in DOM(text).links if p.startswith('?from=')]
        self.assertGreaterEqual(len(links),2)
        for link in links:
            self.assertEqual(parse_qs(urlsplit(link).query).get('q'),['alpha & beta'],'R-CHATS-QUERY: exact search survives paging')
    def test_zero_match_with_parse_error_is_disclosed(self):
        query='synthetic absent search'
        window=history.search(str(self.transcript),query)
        self.assertEqual((window.counts.lines,window.counts.turns,window.counts.unparseable,window.matched),(132,130,1,0))
        agent=self.registered();app,client=self.client()
        text=client.get('/chats/'+agent.agent_id,query_string={'q':query}).get_data(as_text=True)
        self.assertNotIn('That is a real zero, not an',text,'R-CHATS-PARSE-ZERO: incomplete parsing cannot claim an error-free zero')
        self.assertIn('This search is incomplete.',text)
        for params in ({'q':query},{'q':'alpha & beta'},{}):
            page=client.get('/chats/'+agent.agent_id,query_string=params).get_data(as_text=True)
            self.assertIn('1 of 132 records could not be read.',page)
    def test_fully_parsed_zero_match_control(self):
        lines=self.transcript.read_text().splitlines(keepends=True)
        self.assertEqual(lines[-1],'{invalid\n')
        self.transcript.write_text(''.join(lines[:-1]))
        query='synthetic absent search'
        window=history.search(str(self.transcript),query)
        self.assertEqual((window.counts.lines,window.counts.turns,window.counts.unparseable,window.matched),(131,130,0,0))
        agent=self.registered();app,client=self.client()
        text=client.get('/chats/'+agent.agent_id,query_string={'q':query}).get_data(as_text=True)
        self.assertIn('That is a real zero, not an',text,'R-CHATS-PARSE-CONTROL: a fully parsed search retains its complete-zero disclosure')
        self.assertNotIn('This search is incomplete.',text)
        self.assertNotIn('records could not be read.',text)
    def test_page_ranges_and_last_page(self):
        agent=self.registered();app,client=self.client()
        first=client.get('/chats/'+agent.agent_id).get_data(as_text=True)
        middle=client.get('/chats/'+agent.agent_id+'?from=50').get_data(as_text=True)
        last=client.get('/chats/'+agent.agent_id+'?from=100').get_data(as_text=True)
        self.assertIn('turns 1&ndash;50',first)
        self.assertIn('turns 51&ndash;100',middle)
        self.assertIn('turns 101&ndash;130',last)
        self.assertIn('That is the end of this conversation as recorded.',last)
    def test_markup_is_text_and_long_fold_has_summary_first(self):
        with self.transcript.open('a') as out:
            out.write(json.dumps({'type':'user','message':{'content':'<script>alert(1)</script> contenteditable WebSocket'}})+'\n')
        agent=self.registered();app,client=self.client()
        text=client.get('/chats/'+agent.agent_id+'?from=100').get_data(as_text=True)
        dom=DOM(text)
        self.assertIn('&lt;script&gt;',text)
        self.assertNotIn('script',dom.tags)
        self.assertNotIn('contenteditable',dom.attrs)
        self.assertFalse({'form','input','textarea','button'} & set(dom.tags))
        self.assertGreater(dom.tags.count('details'),0)
        for i,tag in enumerate(dom.tags):
            if tag == 'details': self.assertEqual(dom.tags[i+1],'summary')
    def test_view_preserves_transcript_fixture(self):
        before=hashlib.sha256(self.transcript.read_bytes()).digest()
        agent=self.registered();app,client=self.client()
        self.assertEqual(client.get('/chats/'+agent.agent_id).status_code,200)
        self.assertEqual(hashlib.sha256(self.transcript.read_bytes()).digest(),before)
    def test_unknown_id_returns_404(self):
        app,client=self.client()
        self.assertEqual(client.get('/chats/unknown-agent').status_code,404)
    def test_invalid_id_returns_404(self):
        app,client=self.client()
        self.assertEqual(client.get('/chats/INVALID').status_code,404)
    def test_missing_transcript_not_claimed_empty(self):
        agent=self.registered(pointer=str(self.root/'missing.jsonl'));app,client=self.client()
        response=client.get('/chats/'+agent.agent_id)
        self.assertEqual(response.status_code,200)
        self.assertIn('it was not there when the registration was checked',response.get_data(as_text=True))
    def test_harness_pointer_discovery_uses_synthetic_home(self):
        sid='11111111-2222-3333-4444-555555555555'
        home=self.root/'synthetic-codex';folder=home/'sessions/2026/09/10';folder.mkdir(parents=True)
        path=folder/('rollout-2026-09-10T00-00-00-'+sid+'.jsonl');path.write_text('{}\n')
        found=harness.codex_session(codex_home=str(home),session_id=sid,workdir=str(self.root))
        self.assertTrue(found.verified)
        self.assertEqual(found.transcript_path,str(path))
        cc=harness.claude_code_session(claude_home=str(self.root/'synthetic-claude'),transcript_path=str(self.transcript),workdir=str(self.root))
        self.assertTrue(cc.verified)


if __name__ == '__main__':
    result=unittest.main(verbosity=2,exit=False)
    print(json.dumps({'synthetic_only':True,'effects':EFFECTS,'real_archive_end_to_end':'UNTESTED','application_mount':'NOT PERFORMED','tests':result.result.testsRun,'failures':len(result.result.failures),'errors':len(result.result.errors)},sort_keys=True))
    raise SystemExit(not result.result.wasSuccessful())
