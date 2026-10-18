"""Recompute current screening from verified saved prices; preserve immutable run files."""
import hashlib,json
from pathlib import Path
from invest_agent.trading.store import instance_lock
from atomic_io import atomic_write
import momentum_review,watch_review,downtrend_approach,recent_trendlines,recent_sperandeo
from invest_agent.trading import momentum_entry,momentum_watch,base_structure,market,sperandeo


def cached(root,screen,snapshot,review,source_hash,options):
    cfg={**review['config'],'trendline_policy':recent_trendlines.VERSION}
    cfg['watchlist']={**cfg['watchlist'],'downtrend_approach':downtrend_approach.settings(options)}
    modules=(momentum_review,watch_review,downtrend_approach,recent_trendlines,recent_sperandeo,
             momentum_entry,momentum_watch,base_structure,market,sperandeo,recent_trendlines.trendln)
    signature=dict(source=source_hash,screen=screen,config=cfg,
        code={m.__name__:hashlib.sha256(Path(m.__file__).read_bytes()).hexdigest() for m in modules})
    key=hashlib.sha256(json.dumps(signature,sort_keys=True).encode()).hexdigest()
    folder=Path(root)/'runtime/recent-screen'/key
    path=folder/'review.json'
    with instance_lock(folder):
        if path.exists():
            record=json.loads(path.read_text(encoding='utf-8'))
            result=record['review']
            if record['sha256']!=hashlib.sha256(json.dumps(result,sort_keys=True).encode()).hexdigest():
                raise ValueError('recent_screen_cache_hash_mismatch')
            return result
        result=momentum_review.build(screen,snapshot,cfg)
        atomic_write(path,json.dumps(dict(review=result,sha256=hashlib.sha256(json.dumps(result,sort_keys=True).encode()).hexdigest()),ensure_ascii=False,allow_nan=False))
        return result
