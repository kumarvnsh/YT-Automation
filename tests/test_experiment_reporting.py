from datetime import datetime, timezone
from src.experiments import select_slot, capture_snapshots

EXPERIMENT = {'id': 'test', 'observation_hours': [24,72,168], 'slots': [{'slot':1,'category':'geography'},{'slot':2,'category':'disaster'}]}

def test_slot_selection_ignores_other_experiments():
    assert select_slot([{'experiment': 'other', 'experiment_slot':1}],EXPERIMENT)['slot'] == 1
    assert select_slot([{'experiment':'test','experiment_slot':1}],EXPERIMENT)['slot'] == 2
    assert select_slot([{'experiment':'test','experiment_slot':i} for i in (1,2)],EXPERIMENT) is None

def test_snapshot_age_and_missing_metrics_are_explicit_and_immutable():
    entries=[{'experiment':'test','experiment_slot':1,'video_id':'v','published_at':'2026-10-01T00:00:00Z'}]
    analytics={'updated':'2026-10-02T02:00:00Z','videos':[{'id':'v','views':100,'analytics_views':100,'analytics_period':{'start_date':'2026-10-01','end_date':'2026-10-02'},'shares':5}]}
    now=datetime(2026,10,2,3,tzinfo=timezone.utc)
    result=capture_snapshots(entries,EXPERIMENT,{'youtube':analytics},[],now)
    assert len(result)==1
    row=result[0]
    assert row['actual_age_hours']==26
    assert row['late'] is True
    assert row['metrics']['shares_per_1000_views']['value']==50
    assert row['metrics']['average_watch_time_seconds']['value'] is None
    assert row['metrics']['average_watch_time_seconds']['reason']
    assert capture_snapshots(entries,EXPERIMENT,{'youtube':analytics},result,now)==result

def test_late_collection_does_not_claim_historical_windows():
    entries=[{'experiment':'test','video_id':'v','published_at':'2026-10-01T00:00:00Z'}]
    source={'updated':'2026-10-09T00:00:00Z','videos':[{'id':'v','views':0,'shares':0}]}
    result=capture_snapshots(entries,EXPERIMENT,{'youtube':source},[],datetime(2026,10,9,tzinfo=timezone.utc))
    assert [r['target_age_hours'] for r in result]==[168]
    assert result[0]['metrics']['shares_per_1000_views']['value'] is None

def test_youtube_optional_metrics_preserve_missing_values():
    from scripts.export_analytics import _retention_and_subs
    class Client:
        def reports(self): return self
        def query(self, **kwargs):
            assert 'averageViewDuration' in kwargs['metrics'] and 'shares' in kwargs['metrics']
            return self
        def execute(self): return {'rows': [['v', 90, None, 12.5, 2]]}
    row = _retention_and_subs(Client(), ['v'], '2026-10-01', '2026-10-02')['v']
    assert row['subs_gained'] is None
    assert row['average_watch_time_seconds'] == 12.5
    assert row['shares'] == 2

def test_meta_unsupported_metrics_are_explicit():
    from scripts.export_meta_analytics import _experiment_metrics
    row = _experiment_metrics({'views':100}, 'instagram')
    assert row['shares'] is None
    assert 'not collected' in row['unavailable_metrics']['shares']

def test_youtube_hidden_view_count_not_a_zero_denominator():
    from scripts.export_analytics import _video_stats
    class Client:
        def videos(self): return self
        def list(self, **kwargs): return self
        def execute(self): return {'items':[{'id':'v','snippet':{},'statistics':{}}]}
    assert _video_stats(Client(), ['v'])[0]['views'] is None

def test_youtube_rates_require_matching_analytics_denominator():
    from src.experiments import metric_values
    row=metric_values({'views':1000,'shares':5,'subs_gained':2}, platform='youtube')
    assert row['shares_per_1000_views']['value'] is None
    row=metric_values({'views':1000,'analytics_views':100,'shares':5,'subs_gained':2,
                      'analytics_period':{'start_date':'2026-10-01','end_date':'2026-10-02'}},platform='youtube')
    rate=row['shares_per_1000_views']
    assert rate['value']==50
    assert rate['denominator']['source']=='youtube_analytics_report.views'
    assert rate['denominator']['period']['end_date']=='2026-10-02'


def test_youtube_report_exports_its_own_view_denominator():
    from scripts.export_analytics import _retention_and_subs
    class Client:
        def reports(self): return self
        def query(self, **kwargs):
            assert kwargs['metrics'].endswith(',views')
            return self
        def execute(self): return {'rows':[['v',90,2,12.5,5,100]]}
    assert _retention_and_subs(Client(),['v'],'2026-10-01','2026-10-02')['v']['analytics_views']==100
