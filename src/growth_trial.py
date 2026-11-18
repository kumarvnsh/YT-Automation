"""Finite Histold editorial trial and honest observations at comparable ages."""
from datetime import datetime,timezone
import json
import statistics
import re
from pathlib import Path

LANES=(
    {'id':'ingenious_solutions','name':'Ingenious Solutions',
     'direction':'One documented dangerous or expensive problem and the unexpected mechanism people used to solve it. Show how the solution worked; do not exaggerate danger.'},
    {'id':'decisions','name':'Decisions With Consequences',
     'direction':'One documented choice and its surprising, supported consequence. Explain the causal chain; never claim a sole cause without evidence.'},
    {'id':'everyday_origins','name':'Everyday Objects, Unexpected Histories',
     'direction':'A familiar object or habit with an unexpected documented origin or connection. Show the object and explain why its history matters; avoid unsupported first-invention claims.'},
)


def assignment(cfg,ordinal):
    trial=cfg.get('editorial.growth_trial') or {}
    start=trial.get('start_slot')
    if not trial.get('enabled') or not isinstance(start,int): return None
    index=ordinal-start
    if not 0<=index<int(trial.get('posts',42)): return None
    # Shift lanes each three-post day so subject and publishing hour are not locked together.
    lane=LANES[(index%3+index//3)%3]
    return {'ordinal':ordinal,'region':{'id':'global','label':'anywhere in the world',
            'guidance':'Choose a specific culture, country and period because the story is compelling, not to fill a geographic quota.'},
            'series':dict(lane),'duration_variant':'concise','target_seconds':35,
            'story_profile':'evergreen_concise','primary_platform':'youtube',
            'follow_cta':False,'discussion_question':False,
            'growth_trial':{'id':trial['id'],'index':index,'lane':lane['id'],'planned_posts':int(trial.get('posts',42))}}


def selection_brief():
    return ('\nEDITORIAL SELECTION REQUIRED: Before researching the final story, consider THREE distinct original '
            'premises in this assigned lane. Select for immediate understandable surprise, broad viewer relevance, '
            'a concrete opening visual, a satisfying payoff, and evidence on approved source hosts. '
            'Treat these as editorial judgments, never predicted views or proof of virality. '
            'Return an editorial_selection object alongside the evidence packet with candidates: '
            '[{premise,opening_visual,payoff,viewer_reason,rejection_reason}], selected_index:0|1|2, '
            'and selection_reason. Rejected candidates need a specific rejection_reason. '
            'The selected premise must match the production claims. Do not repeat recent subjects. '
            'Prefer maps, archival objects and simple explanatory diagrams we can render; no invented historical imagery as evidence.')


def validate_selection(packet):
    selection=packet.get('editorial_selection') or {}
    candidates=selection.get('candidates')
    index=selection.get('selected_index')
    if not isinstance(candidates,list) or len(candidates)!=3 or type(index) is not int or not 0<=index<3:
        raise ValueError('Editorial trial requires three candidate premises and a selected_index before production')
    for i,item in enumerate(candidates):
        required=('premise','opening_visual','payoff','viewer_reason') if i==index else ('premise','rejection_reason')
        if not isinstance(item,dict) or any(not isinstance(item.get(k),str) or not item[k].strip() for k in required):
            raise ValueError('Editorial selection is missing a premise, opening visual, payoff or rejection reason')
        if re.search(r'candidate\s*\d|not researched|not determined|a (?:second|third) original premise',item['premise'],re.I):
            raise ValueError('Candidate placeholders are not real editorial alternatives')
    if len({item['premise'].strip().casefold() for item in candidates})!=3:
        raise ValueError('Candidate premises must be distinct')
    if not isinstance(selection.get('selection_reason'),str) or not selection['selection_reason'].strip():
        raise ValueError('Editorial selection must explain the choice')
    return selection


CHECKPOINTS={'24h':(24,3),'72h':(72,6),'7d':(168,12)}


def record(root,index,youtube,facebook,now=None):
    """First observations in bounded age windows; never backfill lifetime counts as old checkpoints."""
    now=now or datetime.now(timezone.utc)
    root=Path(root);path=root/'data/private/growth-trial-observations.json'
    try: data=json.loads(path.read_text(encoding='utf-8'))
    except (OSError,ValueError): data={'videos':{}}
    for entry in index:
        trial=entry.get('growth_trial')
        if not trial or not entry.get('published_at') or not entry.get('video_id'): continue
        published=datetime.fromisoformat(entry['published_at'].replace('Z','+00:00'))
        video=data['videos'].setdefault(entry['video_id'],{'trial':trial,'title':entry.get('title'),
                    'published_at':entry['published_at'],'checkpoints':{}})
        for platform,report,identity in [('youtube',youtube,entry['video_id']),('facebook',facebook,str(entry.get('facebook_id')))]:
            try: stamp=datetime.fromisoformat(report['updated'].replace('Z','+00:00'))
            except (KeyError,TypeError,ValueError): continue
            if not 0<=(now-stamp).total_seconds()<=3*3600: continue
            age=(stamp-published).total_seconds()/3600
            row=next((r for r in report.get('videos',[]) if str(r.get('id'))==identity),None)
            if row is None: continue
            for name,(target,tolerance) in CHECKPOINTS.items():
                if not target<=age<=target+tolerance: continue
                checkpoint=video['checkpoints'].setdefault(name,{})
                if platform in checkpoint: continue
                fields=('views','engaged_views','avg_view_pct','average_watch_time_seconds','subs_gained','shares') if platform=='youtube' else ('plays','initial_plays','replays','average_watch_time_seconds','shares','views_metric')
                checkpoint[platform]={'observed_at':stamp.isoformat(),'age_hours':round(age,2),
                    'metrics':{k:row.get(k) for k in fields},'note':'Observed export; detailed platform analytics may still be processing.'}
    data['updated']=now.isoformat()
    path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix('.tmp');temp.write_text(json.dumps(data,indent=2),encoding='utf-8');temp.replace(path)
    return summarize(data,now)


def summarize(data,now=None):
    now=now or datetime.now(timezone.utc)
    groups={}
    for video in data.get('videos',{}).values():
        trial=video['trial'];group=groups.setdefault((trial['id'],trial['lane']),{'id':trial['id'],'lane':trial['lane'],'published':0,'youtube':[],'facebook':[]})
        group['published']+=1
        for platform,metric in [('youtube','views'),('facebook','plays')]:
            value=video.get('checkpoints',{}).get('72h',{}).get(platform,{}).get('metrics',{}).get(metric)
            if isinstance(value,(int,float)) and not isinstance(value,bool): group[platform].append(value)
    rows=[]
    for group in groups.values():
        row={k:group[k] for k in ('id','lane','published')}
        for platform in ('youtube','facebook'):
            vals=group[platform];row[platform]={'measured_72h':len(vals),'median_72h':statistics.median(vals) if vals else None}
        rows.append(row)
    return {'updated':now.isoformat(),'lanes':rows,'status':'collecting',
            'note':'No automatic winner or schedule change. Compare platforms separately; missing checkpoints are unknown, not zero.'}
