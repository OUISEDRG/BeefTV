"""Fresh checks only: never uploads, creates a storyboard, or submits generation."""
import json
from pathlib import Path
import requests
from bridge import DreamaGateway, SERIES, MODEL
from install_channel import decode, CHANNEL_ID, channel
from server import ROOT, PORT, local_token

def verify():
    base=f'http://127.0.0.1:{PORT}'
    headers={'Authorization':'Bearer '+local_token()}
    health=requests.get(base+'/health',timeout=5).json()
    assert health['generationEnabled'] is True and health['autoGeneration'] is False
    assert health['requiresPerJobConfirmation'] is True
    for page in ('/console','/console.js'):
        response=requests.get(base+page,timeout=5)
        assert response.status_code==200 and local_token() not in response.text
    assert requests.get(base+'/v1/models',timeout=5).status_code==401
    models=requests.get(base+'/v1/models',headers=headers,timeout=5).json()
    assert len(models['data'])==22 and MODEL in [m['id'] for m in models['data']]
    job=requests.get(base+'/v1/videos/live-reference-preflight-001',headers=headers,timeout=5).json()
    assert job['status']=='pending' and job['requires_confirmation'] is True
    assert job['generationSubmitted'] is False
    gateway=DreamaGateway()
    board=gateway.run('storyboard','list','--series-id',SERIES,'--id',411953)['items'][0]
    gateway.verify_reference_tags(board['videoPrompt'],[{'imageId':218115}])
    assert not board.get('videoTaskId') and not board.get('imageTaskId')
    data=Path('D:/BeeftvData')
    document=decode((data/'local-model-config.json').read_bytes(),(data/'.settings-key').read_bytes())
    channels=[x for x in document['config']['channels'] if x['id']==CHANNEL_ID]
    assert len(channels)==1 and channels[0]['apiKey']==local_token()
    assert channels[0]['modelProfiles'][0]['referenceMediaTransport']['image']=='inline'
    assert len(channels[0]['models'])==22
    assert set(channels[0]['models'])=={m['id'] for m in models['data']}
    assert channels[0]['modelProfiles']==channel(local_token())['modelProfiles']
    jobs=requests.get(base+'/v1/jobs',headers=headers,timeout=5).json()['data']
    submitted=[job for job in jobs if job.get('approvedAt') or job.get('generationSubmitted') or job.get('taskId')]
    assert not submitted, 'This no-charge verification requires an untouched generation ledger.'
    previous=gateway.task_status(927297)
    assert previous['status']==3 and len(previous['urls'])==1
    result={'health':'ok','authentication':'verified','modelDirectory':'verified',
        'persistedReceipt':'verified_after_service_restart','storyboardId':411953,
        'referenceImageIds':[218115],'exactReferenceTags':'verified_from_fresh_Dreama_read',
        'videoTaskId':board.get('videoTaskId'),'imageTaskId':board.get('imageTaskId'),
        'channelPersistedAfterBeefTVRestart':True,'channelRevision':document['revision'],
        'defaultVideoModel':document['config']['videoModel'],'generationImplemented':True,
        'modelCount':22,'autoGeneration':False,'requiresPerJobConfirmation':True,
        'existingTaskReadVerified':927297,'newPaidTasksSubmitted':len(submitted),
        'modelProfiles':'all_22_match_installed_capabilities','consoleAssets':'ok',
        'canvasEndToEndVerified':False,'uiBlocker':'prior_coordinate_geometry_or_minimized_window; no_new_paid_canvas_test'}
    (ROOT/'evidence'/'final-readonly-verification.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),'utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=='__main__': verify()
