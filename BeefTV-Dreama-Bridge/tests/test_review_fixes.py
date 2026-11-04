import copy
import json
import sqlite3
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from bridge import Bridge, BridgeError, DreamaGateway
from generation import Generation
from auto_execution import AutoExecution
from tests.test_bridge import request
from tests.test_generation import GenerationGateway


class AtomicCancellationTests(unittest.TestCase):
    def test_second_writer_cannot_cancel_between_checked_flag_and_submit_intent(self):
        with tempfile.TemporaryDirectory() as root:
            gateway=GenerationGateway(); bridge=Bridge(root,gateway)
            job=bridge.prepare(request(),'one'); flow=Generation(bridge)
            engine=AutoExecution(bridge,lambda _:flow,{'mode':'manual'})
            with bridge.db() as db:
                db.execute('INSERT INTO preparation_requests(key,payload) VALUES (?,?)',('one',''))
            original_db=bridge.db; interleaving=[]
            class Connection:
                def __init__(self,connection): self.connection=connection
                def execute(self,sql,args=()):
                    if sql=='UPDATE jobs SET result=? WHERE key=?' and json.loads(args[0]).get('status')=='submitting':
                        # A genuinely separate SQLite writer attempts to commit
                        # cancellation precisely after the first writer's read.
                        other=sqlite3.connect(bridge.db_path,timeout=0)
                        try:
                            other.execute('BEGIN IMMEDIATE')
                            other.execute('UPDATE preparation_requests SET cancel_requested=1 WHERE key=?',('one',))
                            other.commit();interleaving.append('cancel-committed')
                        except sqlite3.OperationalError:
                            interleaving.append('submit-transaction-won')
                        finally: other.close()
                    return self.connection.execute(sql,args)
            @contextmanager
            def observed_db():
                with original_db() as db: yield Connection(db)
            with patch.object(bridge,'db',observed_db):
                flow.approve('one',job['quoteId'],job['pointsEstimate'])
            self.assertEqual(interleaving,['submit-transaction-won'])
            self.assertEqual(gateway.calls.count('paid-submit'),1)
            with self.assertRaises(BridgeError):engine.cancel('one')
            self.assertEqual(bridge.get('one')['status'],'queued')
            with bridge.db() as db:
                self.assertEqual(db.execute('SELECT cancel_requested FROM preparation_requests WHERE key=?',('one',)).fetchone()[0],0)

    def test_committed_second_connection_cancel_prevents_submission(self):
        with tempfile.TemporaryDirectory() as root:
            gateway=GenerationGateway();bridge=Bridge(root,gateway)
            job=bridge.prepare(request(),'one');flow=Generation(bridge)
            with bridge.db() as db:
                db.execute('INSERT INTO preparation_requests(key,payload) VALUES (?,?)',('one',''))
            with sqlite3.connect(bridge.db_path) as cancelling:
                cancelling.execute('BEGIN IMMEDIATE')
                cancelling.execute('UPDATE preparation_requests SET cancel_requested=1 WHERE key=?',('one',))
            cancelling.close()
            with self.assertRaises(BridgeError):flow.approve('one',job['quoteId'],job['pointsEstimate'])
            self.assertNotIn('paid-submit',gateway.calls)


def fresh_catalogs():
    root=Path(__file__).parents[1]
    snapshots={kind:json.loads((root/file).read_text('utf-8'))['items'] for kind,file in
               [('video','catalog.json'),('image','image-catalog.json'),('extension','extension-catalog.json')]}
    row=copy.deepcopy(next(item for item in snapshots['video'] if item['eid']=='50347e68-2b4c-49da-8c45-426432bba793'))
    row.update(eid='fresh-eid',alias='1080P',videoTokens='[{"duration":5}]')
    snapshots['video'].append(row)
    return snapshots


class CatalogRecoveryTests(unittest.TestCase):
    def test_failed_consumer_validation_preserves_valid_catalog_through_restart(self):
        from integration import catalog
        from catalog_cache import CACHE_NAME
        from tests.test_workbench import BoundGateway
        import model_catalog,image_flow,extension
        class Gateway(BoundGateway):
            def catalogs(self):return fresh_catalogs()
        for kind in ('video','extension','image'):
            with self.subTest(kind=kind),tempfile.TemporaryDirectory() as root,patch.object(model_catalog,'LIVE_ITEMS',None),patch.object(image_flow,'LIVE_ITEMS',None),patch.object(extension,'LIVE_ITEMS',None):
                gateway=Gateway();Bridge(root,gateway)
                original=catalog(gateway=gateway,state_root=root)
                before=(Path(root)/CACHE_NAME).read_bytes()
                memory=copy.deepcopy((model_catalog.LIVE_ITEMS,image_flow.LIVE_ITEMS,extension.LIVE_ITEMS))
                invalid=fresh_catalogs()
                if kind=='image':
                    next(item for item in invalid[kind] if item.get('isExternal')==0)['proportionList']=[]
                else:
                    row=copy.deepcopy(next(item for item in invalid[kind] if item['name']=='九梦 2.5 Pro' and item['alias'].upper()=='480P'))
                    row['eid']='duplicate-480-eid';invalid[kind].append(row)
                with patch.object(gateway,'catalogs',return_value=invalid),self.assertRaises(BridgeError):
                    catalog(gateway=gateway,state_root=root)
                self.assertEqual((Path(root)/CACHE_NAME).read_bytes(),before)
                self.assertEqual((model_catalog.LIVE_ITEMS,image_flow.LIVE_ITEMS,extension.LIVE_ITEMS),memory)
                self.assertEqual(catalog(),original)
                model_catalog.LIVE_ITEMS=image_flow.LIVE_ITEMS=extension.LIVE_ITEMS=None
                Bridge(root,gateway)
                self.assertEqual(catalog(),original)
                self.assertEqual(model_catalog.resolve_model('50347e68-2b4c-49da-8c45-426432bba793','1080p')['id'],'fresh-eid')

    def test_startup_rejects_consumer_invalid_cache_without_changing_memory(self):
        from catalog_cache import persist
        from tests.test_workbench import BoundGateway
        import model_catalog,image_flow,extension
        invalid=fresh_catalogs()
        row=copy.deepcopy(next(item for item in invalid['video'] if item['eid']=='50347e68-2b4c-49da-8c45-426432bba793'))
        row['eid']='duplicate-480-eid';invalid['video'].append(row)
        with tempfile.TemporaryDirectory() as root,patch.object(model_catalog,'LIVE_ITEMS',None),patch.object(image_flow,'LIVE_ITEMS',None),patch.object(extension,'LIVE_ITEMS',None):
            # Reproduce the former on-disk format with a valid checksum but a
            # catalog that cannot be consumed by normal profile construction.
            persist(root,invalid)
            with self.assertRaises(BridgeError):Bridge(root,BoundGateway())
            self.assertIsNone(model_catalog.LIVE_ITEMS)
            self.assertIsNone(image_flow.LIVE_ITEMS)
            self.assertIsNone(extension.LIVE_ITEMS)

    def test_global_video_profile_uses_project_ratio_source_not_a_fixed_canvas(self):
        from install_channel import channel
        for profile in channel('offline')['modelProfiles']:
            video=profile['capabilityConfig']['video']
            self.assertEqual(video['ratios'],[])
            self.assertEqual(video['defaultRatio'],'')
            self.assertEqual(video['ratioSource'],'project')

    def test_actual_project_ratio_is_accepted_without_invented_model_ratio_list(self):
        from tests.test_workbench import metadata,BoundGateway
        binding=metadata();binding['aspectRatio']='2:3'
        with tempfile.TemporaryDirectory() as root:
            job=Bridge(root,BoundGateway()).prepare({**request(),'aspect_ratio':'2:3','dreama':binding},'ratio')
            self.assertEqual(job['bindingSnapshot']['aspectRatio'],'2:3')

    def test_missing_quote_cannot_make_automatic_deadline_look_paused(self):
        from ledger_status import inspect_ledger
        with tempfile.TemporaryDirectory() as root:
            bridge=Bridge(root,GenerationGateway());job=bridge.prepare(request(),'deadline')
            job.pop('quoteId');job['autoExecuteAt']=123
            bridge.update('deadline',job)
            self.assertFalse(inspect_ledger(root)['safeToReplace'])

    def test_failed_atomic_cache_replacement_preserves_previous_verified_cache(self):
        from integration import catalog
        from catalog_cache import CACHE_NAME
        from tests.test_workbench import BoundGateway
        import model_catalog,image_flow,extension
        class Gateway(BoundGateway):
            def catalogs(self):return fresh_catalogs()
        with tempfile.TemporaryDirectory() as root,patch.object(model_catalog,'LIVE_ITEMS',None),patch.object(image_flow,'LIVE_ITEMS',None),patch.object(extension,'LIVE_ITEMS',None):
            gateway=Gateway();bridge=Bridge(root,gateway);catalog(gateway=gateway,state_root=bridge.root)
            before=(Path(root)/CACHE_NAME).read_bytes()
            with patch('catalog_cache.os.replace',side_effect=OSError('interrupted')),self.assertRaises(OSError):
                catalog(gateway=gateway,state_root=bridge.root)
            self.assertEqual((Path(root)/CACHE_NAME).read_bytes(),before)
            model_catalog.LIVE_ITEMS=image_flow.LIVE_ITEMS=extension.LIVE_ITEMS=None
            Bridge(root,gateway)
            self.assertEqual(model_catalog.resolve_model('50347e68-2b4c-49da-8c45-426432bba793','1080p')['id'],'fresh-eid')

    def test_new_image_snapshot_rechecks_live_capability_without_old_cache_membership(self):
        from image_flow import ImageGateway,build_image_models
        from tests.test_workbench import metadata
        rows=fresh_catalogs()['image']
        row=copy.deepcopy(next(item for item in rows if item['eid']=='7fa38b0a-d222-4a8b-a605-7b55a8bc807a'))
        row['eid']='fresh-image';spec=build_image_models([row])['fresh-image']
        job={'kind':'image','model':'fresh-image','modelSnapshot':spec,'identity':{'userId':17},
             'bindingSnapshot':{**metadata(),'identity':{'userId':17}},'references':[],
             'resolution':'3K','aspect_ratio':'1:1','prompt':'plain'}
        gateway=ImageGateway()
        with patch.object(gateway,'identity',return_value={'userId':17}),patch.object(gateway,'verify_project'),patch.object(gateway,'run',return_value={'id':91,'promptMode':'drama'}),patch.object(gateway,'_invoke',return_value={'items':[row]}) as invoke,patch.object(gateway,'inspect_board',return_value={}):
            gateway.check_ready(job)
            self.assertEqual(invoke.call_args.args,('capability','list','--kind','image'))

    def test_refresh_prepare_restart_existing_task_readiness_is_read_only(self):
        from integration import catalog
        from tests.test_workbench import BoundGateway,metadata
        import model_catalog,image_flow,extension
        class Gateway(BoundGateway):
            def catalogs(self):return fresh_catalogs()
        with tempfile.TemporaryDirectory() as root,patch.object(model_catalog,'LIVE_ITEMS',None),patch.object(image_flow,'LIVE_ITEMS',None),patch.object(extension,'LIVE_ITEMS',None):
            gateway=Gateway();bridge=Bridge(root,gateway);catalog(gateway=gateway,state_root=bridge.root)
            data={**request(),'model':'50347e68-2b4c-49da-8c45-426432bba793','resolution':'1080p','dreama':metadata()}
            original=bridge.prepare(data,'prepared-fresh')
            model_catalog.LIVE_ITEMS=image_flow.LIVE_ITEMS=extension.LIVE_ITEMS=None
            restarted=Bridge(root,gateway);job=restarted.get('prepared-fresh');native=DreamaGateway()
            with patch.object(native,'identity',return_value={'userId':17}),patch.object(native,'verify_project'),patch.object(native,'run',return_value={'id':91,'promptMode':'drama','proportion':'9:16'}),patch.object(native,'_invoke',return_value={'items':fresh_catalogs()['video']}) as invoke,patch.object(native,'inspect_board',return_value={}),patch.object(native,'check_character_references'):
                native.check_ready(job)
                self.assertEqual(invoke.call_args.args,('capability','list','--kind','video'))
            self.assertEqual(job,original);self.assertEqual(job['model'],'fresh-eid')
            self.assertEqual(job['status'],'prepared')

    def test_invalid_refresh_does_not_replace_last_verified_persistent_cache(self):
        from integration import catalog
        from tests.test_workbench import BoundGateway
        from catalog_cache import CACHE_NAME
        import model_catalog,image_flow,extension
        class Gateway(BoundGateway):
            def catalogs(self):return fresh_catalogs()
        with tempfile.TemporaryDirectory() as root,patch.object(model_catalog,'LIVE_ITEMS',None),patch.object(image_flow,'LIVE_ITEMS',None),patch.object(extension,'LIVE_ITEMS',None):
            gateway=Gateway();bridge=Bridge(root,gateway);catalog(gateway=gateway,state_root=bridge.root)
            original=(Path(root)/CACHE_NAME).read_bytes()
            with patch.object(gateway,'catalogs',return_value={'video':[]}),self.assertRaises(BridgeError):
                catalog(gateway=gateway,state_root=bridge.root)
            self.assertEqual((Path(root)/CACHE_NAME).read_bytes(),original)
            self.assertEqual(model_catalog.resolve_model('50347e68-2b4c-49da-8c45-426432bba793','1080p')['id'],'fresh-eid')

    def test_refresh_saved_selection_resolves_after_bridge_restart(self):
        from integration import catalog
        from model_catalog import resolve_model
        from tests.test_workbench import BoundGateway
        import model_catalog,image_flow,extension
        class Gateway(BoundGateway):
            def catalogs(self): return fresh_catalogs()
        with tempfile.TemporaryDirectory() as root,patch.object(model_catalog,'LIVE_ITEMS',None),patch.object(image_flow,'LIVE_ITEMS',None),patch.object(extension,'LIVE_ITEMS',None):
            gateway=Gateway();bridge=Bridge(root,gateway)
            catalog(gateway=gateway,state_root=bridge.root)
            model_catalog.LIVE_ITEMS=image_flow.LIVE_ITEMS=extension.LIVE_ITEMS=None
            Bridge(root,gateway)
            self.assertEqual(resolve_model('50347e68-2b4c-49da-8c45-426432bba793','1080p')['id'],'fresh-eid')

    def test_frozen_currently_supported_model_does_not_depend_on_old_local_membership(self):
        gateway=DreamaGateway()
        spec={'id':'fresh-eid','resolution':'1080P','durations':[5]}
        job={'status':'submitting','approvedAt':1,'model':'fresh-eid','duration':5,'storyboardId':9,
             'modelSnapshot':spec}
        with patch.object(gateway,'_invoke',return_value={'taskId':1}) as invoke:
            gateway.submit_video(job)
        self.assertEqual(invoke.call_args.args[invoke.call_args.args.index('--llm')+1],'fresh-eid')
