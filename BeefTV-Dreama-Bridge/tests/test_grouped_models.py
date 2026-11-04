import tempfile
import unittest
import copy
from unittest.mock import patch
from bridge import Bridge,BridgeError,DreamaGateway,validate_request
from install_channel import channel
from install_extension import channel as extension_channel
from tests.test_bridge import FakeGateway
from tests.test_extension import FakeGateway as ExtGateway, request as extension_request
from extension import ExtensionFlow,validate_extension_request

FAMILIES=[
    ('九梦 2.5 Pro','50347e68-2b4c-49da-8c45-426432bba793','20492049-caa9-46db-b1bb-89723b409504',30),
    ('九梦 2.0 Mini','ef6dd6a2-d0cf-4a55-bf06-af8beed51cb5','0682692b-0682-4433-9b96-a7cd2ea5dd71',15),
    ('九梦 2.0 Fast','edf08924-e258-4b86-b60c-08a6a65aa555','204350fe-23c6-4424-8c61-8d77c337345e',15),
    ('九梦 2.0 Pro','e38e3d2a-a32c-4a3e-9f9c-406b7d1e47fd','7573d384-0128-4424-9e90-8a0291b9274b',15),
]

class GroupedModelsTests(unittest.TestCase):
    def test_generation_menu_has_only_four_families_and_resolution_in_settings(self):
        profiles=channel('test')['modelProfiles']
        self.assertEqual([p['displayName'] for p in profiles],[r[0] for r in FAMILIES])
        for p,(_,low,high,maximum) in zip(profiles,FAMILIES):
            self.assertEqual(p['model'],low)
            v=p['capabilityConfig']['video']
            self.assertEqual(v['resolutions'],['480p','720p']);self.assertEqual(v['defaultResolution'],'480p')
            self.assertEqual(max(v['duration']['values']),maximum)
    def test_resolution_freezes_the_exact_eid_for_quote_and_paid_command(self):
        for name,low,high,_ in FAMILIES:
            for resolution,eid in [('480p',low),('720p',high)]:
                with self.subTest(name=name,resolution=resolution),tempfile.TemporaryDirectory() as root:
                    data={'model':low,'resolution':resolution,'prompt':'原文不改 & <光>','duration':5}
                    gateway=FakeGateway()
                    with patch.object(gateway,'estimate',return_value=42) as quote:
                        job=Bridge(root,gateway).prepare(data,'grouped')
                    self.assertEqual(job['model'],eid);self.assertEqual(job['resolution'],resolution.upper())
                    self.assertEqual(job['prompt'],data['prompt']);quote.assert_called_once_with(5,eid)
                    job.update(status='submitting',approvedAt=1)
                    with patch.object(DreamaGateway,'_invoke',return_value={'taskId':123}) as paid:
                        DreamaGateway().submit_video(job)
                    args=paid.call_args.args
                    self.assertEqual(args[args.index('--llm')+1],eid)
                    self.assertEqual(args[args.index('--resolution')+1],resolution.upper())
    def test_extension_has_one_model_with_both_resolutions_and_routes_the_selected_eid(self):
        profiles=extension_channel('test')['modelProfiles']
        self.assertEqual(len(profiles),1)
        self.assertEqual(profiles[0]['capabilityConfig']['video']['resolutions'],['480p','720p'])
        for resolution,eid in [('480p',FAMILIES[0][1]),('720p',FAMILIES[0][2])]:
            with self.subTest(resolution=resolution),tempfile.TemporaryDirectory() as root:
                data=extension_request();data['resolution']=resolution
                gateway=ExtGateway();flow=ExtensionFlow(Bridge(root),gateway=gateway)
                job=flow.prepare(data,'extension-group')
                self.assertEqual(job['model'],'dreama-extend-'+eid)
                quote=next(p for path,p,_ in gateway.calls if path.endswith('/calcToken'))
                self.assertEqual(quote['eid'],eid)
                flow.approve(job['id'],job['quoteId'],76)
                paid=next(p for _,p,is_paid in gateway.calls if is_paid)
                self.assertEqual(paid['LLM'],eid);self.assertEqual(paid['resolution'],resolution.upper())
    def test_unsupported_resolution_fails_before_any_upstream_write(self):
        for name,low,_,_ in FAMILIES:
            with self.subTest(name=name),self.assertRaises(BridgeError):
                validate_request({'model':low,'resolution':'1080p','prompt':'原文','duration':5})
        data=extension_request();data['resolution']='1080p'
        with self.assertRaises(BridgeError):validate_extension_request(data)
    def test_config_sync_keeps_four_families_and_preserves_other_settings(self):
        import sync_models
        merge=getattr(sync_models,'merge',None)
        self.assertIsNotNone(merge,'Config sync needs a testable grouped-model migration')
        low,high=FAMILIES[0][1:3];cid='dreama-local-preflight'
        document={'schemaVersion':1,'revision':9,'config':{
            'channels':[{'id':cid,'name':'old','baseUrl':'http://127.0.0.1:18765','apiKey':'test','models':[low,high],
                         'modelProfiles':[],'enabled':True,'concurrencyLimit':1}, {'id':'unrelated','apiKey':'private'}],
            'models':[cid+'::'+low,cid+'::'+high,'unrelated::one'],
            'videoModels':[cid+'::'+low,cid+'::'+high,'unrelated::one'],
            'videoModel':cid+'::'+high,'vquality':'720','videoSeconds':'4','imageModel':'unrelated::image'}}
        before=copy.deepcopy(document);updated=merge(document,'test')
        self.assertEqual(document,before)
        self.assertEqual(updated['config']['videoModel'],cid+'::'+low)
        self.assertEqual(updated['config']['vquality'],'720')
        self.assertEqual(updated['config']['channels'][1],before['config']['channels'][1])
        self.assertEqual(updated['config']['imageModel'],'unrelated::image')
        self.assertEqual(updated['config']['videoSeconds'],'4')
        self.assertEqual(updated['config']['channels'][0]['models'],[f[1] for f in FAMILIES])
        self.assertEqual(merge(updated,'test'),updated)
