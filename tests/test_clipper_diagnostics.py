import json
from src.clipper_diagnostics import snapshot


def test_snapshot_preserves_success_and_redacts_private_source_log(tmp_path):
    work=tmp_path/'work/scheduler'; work.mkdir(parents=True)
    identity='a'*32
    (work/'state.json').write_text(json.dumps({'phase':'error','manual_job_id':identity,
        'uploads':[{'status':'uploaded','video_id':'published'}]}),encoding='utf-8')
    manual=tmp_path/'work/manual'/identity; manual.mkdir(parents=True)
    (manual/'source-inspection.log').write_text('HTTP Error 403: https://media.invalid/?signed=private\nAuthorization: private',encoding='utf-8')
    result=json.loads(snapshot(tmp_path))
    assert result['recent_publications'][0]['status']=='uploaded'
    assert '403' in result['source_inspection_log']
    assert 'private' not in result['source_inspection_log']


def test_missing_local_evidence_does_not_infer_login_failure(tmp_path):
    assert 'do not infer a login failure' in snapshot(tmp_path)
