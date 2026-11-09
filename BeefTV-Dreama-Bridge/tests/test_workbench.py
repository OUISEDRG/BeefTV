from tests.fixtures_budget import intent_fields
import copy
import json
import tempfile
import threading
import unittest
from unittest.mock import patch
import requests
from bridge import Bridge, BridgeError, DreamaGateway, PROJECT, SERIES
from tests.fixtures_budget import FundedBridge as Bridge
from tests.test_bridge import FakeGateway, request


def metadata(project=91, series=92):
    return dict(schemaVersion=1, canvasId='canvas-a', nodeId='node-a', projectId=project,
                seriesId=series, promptMode='drama', aspectRatio='9:16', bindingRevision=3)


class BoundGateway(FakeGateway):
    def validate_binding(self, binding, spec):
        if binding['seriesId'] != 92:
            raise BridgeError('series belongs to another project')
        return {'userId': 17}


class WorkbenchBindingTests(unittest.TestCase):
    def test_offline_guard_forbids_credentials_cli_and_external_network(self):
        from pathlib import Path
        import socket,subprocess
        with self.assertRaisesRegex(RuntimeError,'credential'):
            (Path(tempfile.gettempdir())/'credentials-prod.json').read_text('utf-8')
        with self.assertRaisesRegex(RuntimeError,'network'):
            socket.getaddrinfo('example.com',443)
        with self.assertRaisesRegex(RuntimeError,'CLI'):
            subprocess.run(['Dreama.exe','auth','whoami'],capture_output=True)
    def test_login_change_during_preparation_blocks_next_native_write(self):
        from job_binding import CURRENT
        from types import SimpleNamespace
        gateway=DreamaGateway()
        gateway.credentials=SimpleNamespace(read_text=lambda encoding:'{"accessToken":"offline-test-token"}')
        token=CURRENT.set({**metadata(),'identity':{'userId':17}})
        try:
            with patch.object(gateway,'identity',return_value={'userId':18}),patch('dreama_compat.http.requests.post') as post:
                with self.assertRaises(BridgeError):gateway.post('/storyboard/api/save',{'id':1})
                self.assertEqual(post.call_count,0)
        finally:CURRENT.reset(token)

    def test_live_limit_change_blocks_before_media_writes(self):
        from pathlib import Path
        from model_catalog import resolve_model
        gateway=DreamaGateway();spec=resolve_model(request()['model'])
        row=copy.deepcopy(next(r for r in json.loads((Path(__file__).parents[1]/'catalog.json').read_text('utf-8'))['items'] if r['eid']==spec['id']))
        row['modelLimit']='{"imageMin":0,"imageMax":1,"modelTextLimit":10}'
        with patch.object(gateway,'identity',return_value={'userId':17}),patch.object(gateway,'verify_project'),patch.object(gateway,'_invoke',return_value={'items':[row]}):
            with self.assertRaises(BridgeError):gateway.validate_binding(metadata(),spec)

    def test_preparation_calls_use_context_project_and_series_for_reference_and_board(self):
        gateway=DreamaGateway();calls=[]
        class Native(DreamaGateway):
            def validate_binding(self,binding,spec): return {'userId':17}
            def upload_image(self,*args): return 'https://image'
            def post(self,path,payload): calls.append((path,copy.deepcopy(payload)));return {}
            def run(self,*args):
                calls.append(args)
                if args[:2]==('library','list'):
                    name=args[args.index('--search')+1]
                    return {'items':[{'assetId':4,'name':name,'images':[{'imageId':5,'name':name,'resourceUrl':['https://image']}]}]}
                if args[:2] in (('storyboard','create'),('storyboard','update')):return {}
                if args[:2]==('storyboard','list'):
                    return {'items':[{'id':93,'projectId':91,'seriesId':92,'videoPrompt': '<p>plain</p>'}]}
                if args[:2]==('point','calc'): return {'token':1}
                raise AssertionError(args)
            def render_verbatim_prompt(self,prompt,refs): return '<p>plain</p>',[]
        with tempfile.TemporaryDirectory() as root:
            bridge=Bridge(root,Native())
            # Exercise actual save/estimate and separate actual reference binding.
            job=bridge.prepare({**request(),'images':[],'prompt':'plain','dreama':metadata()},'one')
            from job_binding import CURRENT
            token=CURRENT.set(job['bindingSnapshot'])
            try:bridge.gateway.create_reference('asset','https://image')
            finally:CURRENT.reset(token)
        for call in calls:
            if isinstance(call[1],dict):
                if 'projectId' in call[1]: self.assertEqual(call[1]['projectId'],91)
                if 'seriesId' in call[1]: self.assertEqual(call[1]['seriesId'],92)
            else:
                if '--project-id' in call:self.assertEqual(call[call.index('--project-id')+1],91)
                if '--series-id' in call:self.assertEqual(call[call.index('--series-id')+1],92)

    def test_extension_quote_survives_catalog_removal_using_frozen_model(self):
        from extension import ExtensionGateway
        gateway=ExtensionGateway()
        job={'model':'removed','duration':5,'sourceVideo':{'duration':4},
             'modelSnapshot':{'id':'removed','eid':'frozen-eid','resolution':'720P'},
             'bindingSnapshot':{**metadata(),'identity':{'userId':17}}}
        with patch.object(gateway,'_request',return_value={'token':3}) as call:
            self.assertEqual(gateway.estimate_extension(job),3)
            self.assertEqual(call.call_args.args[1]['eid'],'frozen-eid')

    def test_enhancement_reconciliation_rejects_another_project_even_same_input(self):
        from enhancement import EnhancementGateway
        from tests.test_enhancement import prepared_job,task_row
        value=prepared_job(); value['bindingSnapshot']={**metadata(),'identity':value['identity']}
        row=task_row(value);row['payload'].update(projectId=91,seriesId=99)
        self.assertFalse(EnhancementGateway.matches(row,value))

    def test_mutated_runtime_identity_cannot_diverge_from_frozen_snapshot(self):
        with tempfile.TemporaryDirectory() as root:
            bridge=Bridge(root,BoundGateway()); job=bridge.prepare({**request(),'dreama':metadata()},'one')
            job['identity']={'userId':99}
            with self.assertRaises(BridgeError):bridge.update('one',job)

    def test_read_only_ledger_compatibility_reports_new_bindings_and_active_queue(self):
        with tempfile.TemporaryDirectory() as root:
            bridge=Bridge(root,BoundGateway());bridge.prepare({**request(),'dreama':metadata()},'one')
            try:
                from ledger_status import inspect_ledger
            except ImportError:
                self.fail('Read-only release compatibility entry point is missing')
            state=inspect_ledger(root)
            self.assertEqual(state['ledgerSchemaVersion'],6)
            self.assertEqual(state['minimumReaderVersion'],3)
            self.assertEqual(state['activeCounts'],{'prepared':1})
            self.assertEqual(state['unresolvedCounts'],{})
            self.assertFalse(state['safeToReplace'])

    def test_binding_is_frozen_before_first_remote_effect_and_cannot_be_overwritten(self):
        with tempfile.TemporaryDirectory() as root:
            data={**request(), 'dreama':metadata()}
            bridge=Bridge(root, BoundGateway())
            job=bridge.prepare(data,'bound')
            self.assertEqual(job['bindingSnapshot']['identity'], {'userId':17})
            self.assertEqual((job['projectId'],job['seriesId']), (91,92))
            self.assertEqual(job['modelSnapshot']['id'], data['model'])
            changed=copy.deepcopy(job); changed['bindingSnapshot']['seriesId']=99
            with self.assertRaises(BridgeError): bridge.update('bound',changed)
            changed=copy.deepcopy(job);changed['model']='another-model'
            with self.assertRaises(BridgeError): bridge.update('bound',changed)

    def test_invalid_series_has_zero_remote_effects(self):
        with tempfile.TemporaryDirectory() as root:
            gateway=BoundGateway(); bridge=Bridge(root,gateway)
            with self.assertRaises(BridgeError): bridge.prepare({**request(),'dreama':metadata(series=99)},'bad')
            self.assertEqual(gateway.calls,[])

    def test_paid_cli_and_reconciliation_read_frozen_ids_without_leaking_to_next_job(self):
        gateway=DreamaGateway()
        job={'model':request()['model'],'duration':5,'storyboardId':93,'status':'submitting','approvedAt':1,
             'bindingSnapshot':{**metadata(),'identity':{'userId':17}}, **intent_fields(1)}
        with patch.object(gateway,'_invoke',return_value={'taskId':1}) as invoke:
            gateway.submit_video(job)
            args=invoke.call_args.args
            self.assertEqual(args[args.index('--project-id')+1],91)
            self.assertEqual(args[args.index('--series-id')+1],92)
        with patch.object(gateway,'run',return_value={'items':[{'taskId':4}]}) as run:
            gateway.task_status(4,job)
            self.assertEqual(run.call_args.args[3],91)
            gateway.task_status(4)
            self.assertEqual(run.call_args.args[3],PROJECT)

    def test_restart_migration_keeps_original_binding_and_holds_interrupted_preparation(self):
        with tempfile.TemporaryDirectory() as root:
            bridge=Bridge(root,FakeGateway())
            legacy={'id':'old','status':'preparing','projectId':22,'seriesId':33,'generationSubmitted':False}
            with bridge.db() as db: db.execute('INSERT INTO jobs VALUES (?,?,?)',('old','digest',json.dumps(legacy)))
            restarted=Bridge(root,FakeGateway()); restarted.recover_preparations()
            row=restarted.get('old')
            self.assertEqual(row['status'],'unknown')
            self.assertEqual(row['bindingSnapshot']['projectId'],22)
            self.assertEqual(row['bindingSnapshot']['seriesId'],33)


class WorkbenchQueueTests(unittest.TestCase):
    def test_restart_resumes_only_durable_queue_not_interrupted_side_effects(self):
        from server import make_server
        import hashlib
        with tempfile.TemporaryDirectory() as root:
            bridge=Bridge(root,FakeGateway()); data=request()
            digest=hashlib.sha256(json.dumps(data,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
            with bridge.db() as db:
                for key,status in [('safe','preparation_queued'),('unsafe','preparing')]:
                    job={'id':key,'status':status,'generationSubmitted':False,'projectId':PROJECT,'seriesId':SERIES}
                    db.execute('INSERT INTO jobs VALUES (?,?,?)',(key,digest,json.dumps(job)))
                    db.execute('INSERT INTO preparation_requests(key,payload) VALUES (?,?)',(key,json.dumps(data)))
            server=make_server(bridge,'test',0,policy={'mode':'manual'})
            try:
                server.wait_for_jobs()
                self.assertEqual(bridge.get('safe')['status'],'prepared')
                self.assertEqual(bridge.get('unsafe')['status'],'unknown')
                self.assertEqual(bridge.gateway.calls.count('upload'),1)
            finally: server.server_close()

    def test_cancel_during_preparation_stops_before_next_remote_effect(self):
        from server import make_server
        entered=threading.Event(); release=threading.Event()
        class Slow(FakeGateway):
            def upload_image(self,*args):
                entered.set();release.wait(5);return super().upload_image(*args)
        with tempfile.TemporaryDirectory() as root:
            server=make_server(Bridge(root,Slow()),'test',0,policy={'mode':'manual'})
            try:
                server.submit(request(),'one');self.assertTrue(entered.wait(2))
                with self.assertRaises(BridgeError): server.auto_execution.cancel('one')
                release.set();server.wait_for_jobs()
                self.assertEqual(server.bridge.get('one')['status'],'prepared')
                self.assertIn('save',server.bridge.gateway.calls)
            finally: release.set();server.server_close()

    def test_cancel_wins_atomic_last_submit_transition(self):
        from server import make_server
        from tests.test_generation import GenerationGateway
        with tempfile.TemporaryDirectory() as root:
            gateway=GenerationGateway();server=make_server(Bridge(root,gateway),'test',0,policy={'mode':'manual'})
            try:
                server.submit(request(),'one');server.wait_for_jobs();job=server.bridge.get('one')
                def raced(value):
                    from budget_policy import unset_policy
                    server.bridge._budget_policy=unset_policy()
                with patch.object(gateway,'check_ready',side_effect=raced), self.assertRaises(BridgeError):
                    server.flow.approve('one',job['quoteId'],job['pointsEstimate'])
                self.assertNotIn('paid-submit',gateway.calls)
            finally: server.server_close()

    def test_concurrent_requests_queue_durably_and_cancel_before_writes(self):
        from server import make_server
        entered=threading.Event(); release=threading.Event()
        class Slow(FakeGateway):
            def upload_image(self,*args):
                entered.set(); release.wait(5)
                return super().upload_image(*args)
        with tempfile.TemporaryDirectory() as root:
            server=make_server(Bridge(root,Slow()),'test',0,policy={'mode':'manual'})
            try:
                first=server.submit(request(),'one'); self.assertTrue(entered.wait(2))
                second=server.submit(request(),'two')
                self.assertEqual(server.bridge.get(second)['status'],'preparation_queued')
                with self.assertRaises(BridgeError): server.auto_execution.cancel(second)
                release.set(); server.wait_for_jobs()
                self.assertEqual(server.bridge.get(second)['status'],'prepared')
                self.assertEqual(server.bridge.gateway.calls.count('upload'),2)
            finally: release.set(); server.server_close()

    def test_public_tasks_are_canvas_filtered_and_never_contain_internal_payload(self):
        from server import make_server
        with tempfile.TemporaryDirectory() as root:
            server=make_server(Bridge(root,BoundGateway()),'test',0,policy={'mode':'manual'})
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            base='http://127.0.0.1:'+str(server.server_port)
            try:
                server.submit({**request(),'dreama':metadata()},'one'); server.wait_for_jobs()
                self.assertEqual(requests.get(base+'/integration/v1/tasks').status_code,401)
                headers={'Authorization':'Bearer test'}
                response=requests.get(base+'/integration/v1/tasks?canvasId=canvas-a',headers=headers)
                self.assertEqual(response.status_code,200)
                item=response.json()['items'][0]
                self.assertEqual(item['projectId'],91); self.assertEqual(item['nodeId'],'node-a')
                self.assertNotIn('prompt',item);self.assertNotIn('references',item);self.assertNotIn('identity',item)
                self.assertEqual(requests.get(base+'/integration/v1/tasks?canvasId=other',headers=headers).json()['items'],[])
            finally: server.shutdown();server.server_close();thread.join()


class WorkbenchCatalogTests(unittest.TestCase):
    def test_audio_profile_controls_have_native_request_mapping(self):
        from install_modalities import audio_profile
        from audio_flow import audio_models,SEED,CLONE
        from pathlib import Path
        self.assertTrue(audio_profile(audio_models()[SEED])['capabilityConfig']['audio']['pitch']['supported'])
        self.assertFalse(audio_profile(audio_models()[CLONE])['capabilityConfig']['audio']['pitch']['supported'])
        manifest=json.loads((Path(__file__).parents[1]/'dreama-modalities-manifest.json').read_text('utf-8'))
        provider=next(p for p in manifest['contributes']['providers'] if p['id']=='dreama-audio-async')
        self.assertEqual(provider['create']['body']['pitch'],{'$coalesce':[{'$toFloat':{'$ref':'request.extra.audioPitch'}},0]})
        self.assertEqual(provider['create']['body']['volume'],{'$coalesce':[{'$toFloat':{'$ref':'request.extra.audioVolume'}},1]})
    def test_catalog_refresh_exposes_new_implemented_variant_with_its_limits(self):
        import integration
        from pathlib import Path
        class CatalogGateway:
            def catalogs(self):
                video=json.loads((Path(__file__).parents[1]/'catalog.json').read_text('utf-8'))['items']
                row=copy.deepcopy(next(r for r in video if r['eid']=='50347e68-2b4c-49da-8c45-426432bba793'))
                row.update(eid='fresh-eid',alias='1080P',videoTokens='[{"duration":10}]',modelLimit='{"imageMin":1,"imageMax":2,"modelTextLimit":80}')
                return {'video':video+[row],'image':json.loads((Path(__file__).parents[1]/'image-catalog.json').read_text('utf-8'))['items'],
                        'extension':json.loads((Path(__file__).parents[1]/'extension-catalog.json').read_text('utf-8'))['items']}
        import model_catalog,image_flow,extension
        with patch.object(model_catalog,'LIVE_ITEMS',None),patch('dreama_image.LIVE_ITEMS',None),patch('dreama_compat.extension.LIVE_ITEMS',None):
            result=integration.catalog(gateway=CatalogGateway())
            profile=next(p for p in result['channels'][0]['modelProfiles'] if p['model']=='50347e68-2b4c-49da-8c45-426432bba793')
            variant=profile['capabilityConfig']['video']['resolutionVariants']['1080p']
            self.assertEqual(variant['modelId'],'fresh-eid');self.assertEqual(variant['references']['maxImages'],2)
            self.assertEqual(variant['duration']['values'],[10])

    def test_project_and_series_routes_filter_modes_and_validate_before_create(self):
        from integration import get_integration,create_series
        from types import SimpleNamespace
        class Projects:
            def __init__(self): self.calls=[]
            def run(self,*args):
                self.calls.append(args)
                if args[:2]==('project','list'): return {'items':[{'id':91,'name':'one','promptMode':'drama','proportion':'9:16'},{'id':2,'promptMode':'comic'}]}
                if args[:2]==('project','get'): return {'id':args[3],'promptMode':'drama' if args[3]==91 else 'comic','proportion':'9:16'}
                if args[:2]==('series','create'): return {'id':92,'name':args[5],'projectId':args[3]}
                return {'items':[{'id':92,'name':'one','projectId':91}]}
        gateway=Projects();server=SimpleNamespace(bridge=SimpleNamespace(gateway=gateway))
        self.assertEqual(len(get_integration(server,'/projects',{})['items']),1)
        self.assertEqual(get_integration(server,'/series',{'projectId':['91']})['items'][0]['projectId'],91)
        self.assertEqual(create_series(gateway,{'projectId':91,'name':'two'})['item']['id'],92)
        with self.assertRaises(BridgeError):create_series(gateway,{'projectId':2,'name':'bad'})
        self.assertEqual(sum(args[:2]==('series','create') for args in gateway.calls),1)
    def test_new_resolution_variant_preserves_individual_limits(self):
        from model_catalog import group_models,resolve_variant
        rows={'a':dict(id='a',name='family',resolution='480P',durations=[5],minImages=0,maxImages=9,promptMaxChars=100),
              'b':dict(id='b',name='family',resolution='1080P',durations=[10],minImages=1,maxImages=3,promptMaxChars=50,sourceMin=3,sourceMax=20)}
        groups=group_models(rows,['family'])
        self.assertEqual(groups['a']['resolutions'],['480p','1080p'])
        self.assertEqual(resolve_variant(rows,groups,'a','1080p')['maxImages'],3)
        self.assertEqual(groups['a']['resolutionVariants']['1080p']['references']['maxVideoDurationSeconds'],20)

    def test_catalog_profiles_keep_3k_and_namespace_binding_for_every_media_provider(self):
        from install_modalities import image_profile
        from image_flow import image_models
        for spec in image_models().values():
            self.assertEqual(image_profile(spec)['capabilityConfig']['image']['quality']['values'],spec['resolutions'])
        from pathlib import Path
        manifest=json.loads((Path(__file__).parents[1]/'dreama-modalities-manifest.json').read_text('utf-8'))
        for provider in manifest['contributes']['providers']:
            if provider['capabilities']!=['text']:
                self.assertIn('dreama',provider['create']['body'])
