"""Recalculate sector views from verified saved inputs, without altering source runs."""
import hashlib
import json
from pathlib import Path
import sector_observation as so
import sector_signals
from invest_agent.trading.store import instance_lock
from invest_agent.trading.runner import atomic_write


def cached(root, run, config, source, original_observation, review, *, snapshot=None):
    """Reuse the caller's verified snapshot; still verify all pinned files."""
    seed_path = Path(config['sector_manifest'])
    so.checked(seed_path, config['sector_manifest_sha256'], {})
    seed = so.load(seed_path)
    old_market = Path(seed['roots'][0]).resolve()
    inputs = {p:h for p,h in seed['input_hashes'].items() if not Path(p).resolve().is_relative_to(old_market)}
    market_manifest = so.load(Path(source)/'manifest.json')
    inputs.update({str(Path(source)/p):h for p,h in market_manifest.items()})
    inputs[str(Path(source)/'manifest.json')] = so.sha(Path(source)/'manifest.json')
    for path, expected in inputs.items():
        so.checked(path, expected, {})
    applied = original_observation['applied_at']
    run = Path(run)
    run_manifest = so.load(run/'manifest.json')
    so.checked(run/'run.json', run_manifest['output_hashes']['run.json'], inputs)
    basis_rechecks = so.load(run/'run.json').get('basis_rechecks')
    if basis_rechecks:
        basis_inputs = {}
        so.load_basis(Path(seed['roots'][2]), so.checked, so.load, basis_inputs, applied,
                      so.age_issue, rechecks=basis_rechecks)
        for path, expected in basis_inputs.items():
            if run_manifest['input_hashes'].get(path) != expected:
                raise ValueError('projection_unpinned_basis_input')
        inputs.update(basis_inputs)
    if snapshot is None:
        snapshot = so.load(Path(source)/'snapshot.json')
    reference = snapshot.get('classification_refresh')
    if reference:
        from classification_refresh import apply
        refresh_inputs = {}
        apply([], {'classification_refresh':reference}, so.checked, refresh_inputs, applied,
              so.classification_age_issue, so.SECTORS)
        for path, expected in refresh_inputs.items():
            if run_manifest['input_hashes'].get(path) != expected:
                raise ValueError('projection_unpinned_classification_refresh')
        inputs.update(refresh_inputs)
    from adr_metadata import verified as verify_adr
    adr_inputs = {}
    verify_adr(snapshot.get('adr_metadata'), adr_inputs)
    if any(run_manifest['input_hashes'].get(p) != h for p,h in adr_inputs.items()):
        raise ValueError('projection_unpinned_adr_metadata')
    inputs.update(adr_inputs)
    names = ['adr_metadata.py', 'user_classification.py', 'sector_projection.py', 'sector_observation.py', 'sector_signals.py', 'sector_basis.py',
             'wics_classification.py', 'finviz_public_classification.py', 'sector_external_review.py', 'classification_refresh.py']
    key_data = dict(inputs=inputs, seed=config['sector_manifest_sha256'], applied=applied,
                    settings=config.get('sector_states'), review=review,
                    code={n:so.sha(Path(__file__).parent/n) for n in names})
    key = hashlib.sha256(json.dumps(key_data,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    folder = Path(root)/'runtime/sector-projection'/key
    output = folder/'view.json'
    with instance_lock(folder):
        if output.exists():
            meta = so.load(folder/'manifest.json')
            so.checked(output, meta['sha256'], {})
            return so.load(output)
        observation, groups, verified = so.build(Path(source), *[Path(p) for p in seed['roots'][1:]],
                                                applied, allow_universe_changes=True, basis_rechecks=basis_rechecks, snapshot=snapshot)
        # Every dependency read during replay must agree with pinned evidence.
        for path, expected in verified.items():
            if path not in inputs or inputs[path] != expected:
                raise ValueError('projection_unpinned_input:'+path)
        signals = sector_signals.build(snapshot, groups, observation, config.get('sector_states'))
        sector_signals.attach_candidates(signals, review, groups, {'rows':[]})
        result = dict(observation=observation, groups=groups, signals=signals,
                      policy='last_verified_classification_refresh_due_after_7_days', source_run=str(run))
        raw = json.dumps(result,ensure_ascii=False,allow_nan=False)
        atomic_write(output,raw)
        atomic_write(folder/'manifest.json',json.dumps(dict(sha256=hashlib.sha256(raw.encode()).hexdigest(),
                     key=key, input_hashes=inputs, network_calls=0)))
        return result
