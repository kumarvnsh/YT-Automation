"""Refresh Histold's local per-Reel analytics using the existing Meta credential."""
import argparse
import json
import os
import sys
from datetime import datetime,timezone
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from src.config import load_config
from scripts.export_meta_analytics import fetch_facebook


def refresh(root):
    root=Path(root)
    cfg=load_config(root/'config.yaml')
    token=os.environ.get('META_ACCESS_TOKEN') or os.environ.get('META_EARNINGS_ACCESS_TOKEN')
    credential=root.parent/'secrets/meta-earnings-token.txt'
    if not token and credential.exists(): token=credential.read_text(encoding='utf-8-sig').strip()
    if not token: return {'status':'credentials_missing'}
    videos=fetch_facebook(token,str(cfg.get('meta.page_id')),cfg.get('meta.api_version','v21.0'),200)
    result={'status':'connected','updated':datetime.now(timezone.utc).isoformat(),'videos':videos,
            'coverage':'Latest 200 Page videos','view_definition':'Reel plays include replays. Initial plays exclude replays; legacy video views use a different definition.'}
    path=root/'data/private/facebook-reel-analytics.json'
    path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix('.tmp');temp.write_text(json.dumps(result),encoding='utf-8');temp.replace(path)
    return {'status':'connected','videos':len(videos),'reel_metrics_available':sum(v.get('plays') is not None for v in videos)}


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--data-root',required=True,type=Path)
    args=parser.parse_args()
    try: print(json.dumps(refresh(args.data_root)))
    except Exception as exc:
        print('Facebook Reel refresh failed ('+type(exc).__name__+'); previous snapshot preserved.',file=sys.stderr)
        raise SystemExit(1)
