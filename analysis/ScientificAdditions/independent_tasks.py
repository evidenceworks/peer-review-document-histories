"""Independent raw-source reconstruction and exploratory task diagnostics.

No modules from the supplied analytical implementation are imported. All inputs
are archived captures. This audit does not equate a source DOI with an
independent intellectual contribution or use the stratified sample unweighted
as a population estimate.
"""
from pathlib import Path
import argparse, csv, json, datetime as dt, re, collections, hashlib, statistics

def read(p):
    return json.loads(p.read_text(encoding='utf-8'))
def normdoi(v):
    return re.sub(r'^https?://(?:dx\.)?doi\.org/', '', str(v or '').strip(), flags=re.I).lower().rstrip('/')
def timestamp(v):
    if not isinstance(v,str) or not re.search(r'[T ]\d{2}:\d{2}:\d{2}',v): return None
    try:
        x=dt.datetime.fromisoformat(v.replace('Z','+00:00'))
        if x.tzinfo: x=x.astimezone(dt.timezone.utc).replace(tzinfo=None)
        return x.isoformat(timespec='microseconds')
    except ValueError:return None

def doc_outputs(obj):
    """Preserve all identifiers/types/date bindings, inspect distinct source IDs."""
    out=[]
    for sid,step in obj.get('steps',{}).items():
        ins=[{'doi':normdoi(x.get('doi')),'version':x.get('versionIdentifier'),'url':x.get('url')}
             for x in step.get('inputs',[]) if x.get('doi') and not normdoi(x['doi']).startswith('10.7554/elife.')]
        for a in step.get('actions',[]):
            for o in a.get('outputs',[]):
                if o.get('type') not in {'review-article','evaluation-summary','reply'} or not o.get('doi'):continue
                out.append({'doi':normdoi(o['doi']),'role':o['type'],'time':timestamp(o.get('published')),
                    'inputs':ins, 'step':sid})
    uniq={}
    for x in out:
        k=(x['doi'],x['role'],x['time'])
        if k in uniq:
            if uniq[k]['inputs']!=x['inputs']: raise ValueError('Conflicting duplicate source input binding')
        else:uniq[k]=x
    return list(uniq.values())

def header_role(text):
    # Only the opening heading, not any later quoted words or metadata labels.
    s=text.lstrip('\ufeff \n\r\t')[:180]
    first=s.splitlines()[0] if s else ''
    first=re.sub(r'^\s*#+\s*','',first).strip()
    first=first.strip('*_ ')
    if re.match(r"author(?:s[’']?|[’']s)?\s+response\b",first,re.I): return 'reply'
    if re.match(r'elife\s+assessment\b',first,re.I):return 'evaluation-summary'
    if re.match(r'reviewer\s*#?\s*\d+\b',first,re.I):return 'review-article'
    return ''

def eeb_outputs(objs):
    out=[]
    for obj in objs:
        d=obj.get('docmap',obj)
        if (d.get('publisher',{}).get('name') or '').casefold()!='elife':continue
        for s in d.get('steps',{}).values():
            ds={normdoi(i['doi']) for i in s.get('inputs',[]) if i.get('doi')}
            for a in s.get('actions',[]):
                for o in a.get('outputs',[]):
                    if o.get('type') not in {'review','reviews-summary','referee-report','review-article'}:continue
                    text='\n'.join(c.get('text','') for c in o.get('content',[]) if c.get('text'))
                    out.append({'time':timestamp(o.get('published')), 'inputs':ds,'role':header_role(text),
                         'text':text,'doi':normdoi(o.get('doi'))})
    return out

def writecsv(p,rows):
    if not rows:return
    with p.open('w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)

def main(repo,out):
    out.mkdir(parents=True,exist_ok=True)
    plan=read(repo/'Evidence/Provenance/raw_validation_plan.json')
    # Check the original fixed sampling plan against a fresh reconstruction of the draw.
    import random
    frame=list(csv.DictReader((repo/'Results/Provenance/endpoint_family_ledger.csv').open()))
    focal=[x for x in frame if x['has_vor']=='1' and x['exact_MED']=='1']
    rng=random.Random(plan['seed']); expected=[]
    for st in sorted({x['residual_class'] for x in focal}):
        sf=sorted([x for x in focal if x['residual_class']==st],key=lambda x:x['family'])
        expected += [(x['family'],st) for x in rng.sample(sf,min(20,len(sf)))]
    assert expected==[(x['family'],x['stratum']) for x in plan['sample']]
    rows=[];families=[]; ablations=[];raw_hashes=[]
    source_total=collections.Counter();matched_total=collections.Counter()
    roles=['review-article','evaluation-summary','reply']
    for item in plan['sample']:
        f=item['family'];base=repo/'Evidence/Provenance/validation/raw'
        p=base/'elife'/f'{f}.json';sd=read(p);src=doc_outputs(sd)
        raw_hashes.append((str(p.relative_to(repo)),hashlib.sha256(p.read_bytes()).hexdigest()))
        es=[]
        for j,_ in enumerate(item['original_preprint_dois']):
            p=base/'eeb'/f'{f}_{j}.json';es+=eeb_outputs(read(p));raw_hashes.append((str(p.relative_to(repo)),hashlib.sha256(p.read_bytes()).hexdigest()))
        ep=[]
        for endpoint in item['endpoints']:
            p=base/'epmc'/(endpoint.replace('/','_')+'.json');raw=read(p)
            raw_hashes.append((str(p.relative_to(repo)),hashlib.sha256(p.read_bytes()).hexdigest()))
            ep += [(endpoint,r) for r in raw.get('evaluationList',{}).get('evaluation',[])
                   if (r.get('platform') or '').casefold()=='elife' and r.get('type') in {'review','reviews-summary','referee-report','review-article'}]
        got={role:set() for role in roles};total={role:{s['doi'] for s in src if s['role']==role} for role in roles}
        for endpoint,e in ep:
            t=timestamp(e.get('evaluationDate')); doi=normdoi(e.get('doi'))
            cs=[s for s in src if t and s['time']==t and doi in {i['doi'] for i in s['inputs']}]
            chosen=cs[0] if len(cs)==1 else None
            ee=[z for z in es if t and z['time']==t and doi in z['inputs']]
            hs={z['role'] for z in ee if z['role']}
            # Need every candidate to have the same diagnostic role for a safe claim.
            heading=next(iter(hs)) if len(hs)==1 and all(z['role'] in hs for z in ee) else ''
            row={'family':f,'stratum':item['stratum'],'epmc_record':endpoint,'epmc_id':e.get('id'),
                'status':'matched' if chosen else 'unresolved','candidate_n':len(cs),
                'source_doi':chosen['doi'] if chosen else '', 'source_role':chosen['role'] if chosen else '',
                'eeb_candidate_n':len(ee),'heading_only_role':heading,
                'heading_correct':int(bool(chosen and heading and heading==chosen['role'])),
                'heading_wrong':int(bool(chosen and heading and heading!=chosen['role'])),
                'EPMC_nonempty_URL':int(bool(e.get('url')))}
            rows.append(row)
            if chosen:got[chosen['role']].add(chosen['doi']);matched_total[chosen['role']]+=1
            for level in ['microseconds','seconds','day']:
                tx=t if level=='microseconds' else t[:19] if level=='seconds' and t else t[:10] if t else None
                candidates=[s for s in src if s['time'] and tx and
                            (s['time'] if level=='microseconds' else s['time'][:19] if level=='seconds' else s['time'][:10])==tx
                            and doi in {i['doi'] for i in s['inputs']}]
                ablations.append({'family':f,'epmc_id':e.get('id'),'level':level,'candidates':len(candidates),
                   'unique':int(len(candidates)==1), 'same_as_fine':int(bool(chosen and len(candidates)==1 and candidates[0]['doi']==chosen['doi'])), 'fine_matched':int(chosen is not None)})
        fr={'family':f,'stratum':item['stratum'],'N_h':item['stratum_N'],'n_h':item['stratum_n'],'review_rows':len(ep)}
        for role in roles:
            fr[role+'_source']=len(total[role]); fr[role+'_retrieved']=len(got[role]);fr[role+'_missing']=len(total[role]-got[role]);fr[role+'_complete']=int(bool(total[role]) and total[role]<=got[role])
            if ep:source_total[role]+=len(total[role])
        fr['all_roles_complete']=int(all(total[r]<=got[r] for r in roles));families.append(fr)
    prev=list(csv.DictReader((repo/'Results/Provenance/matched_row_repairs.csv').open()))
    key=lambda r:(str(r['family']),r['epmc_record'],str(r['epmc_id']))
    pd={key(r):r for r in prev if r['epmc_type'] in {'review','reviews-summary','referee-report','review-article'}}
    assert len(rows)==len(pd)==462
    for r in rows:
        x=pd[key(r)]; assert r['source_doi']==x['source_doi'] and r['source_role']==x['source_type']
    positive=[x for x in families if x['review_rows']]
    role_summary=[]
    for role in roles:
        n=sum(x[role+'_retrieved'] for x in positive); den=sum(x[role+'_source'] for x in positive)
        role_summary.append({'role':role,'source_objects':den,'retrieved_objects':n,'unmatched_objects':den-n,'recovery_pct':100*n/den,
            'families_with_source':sum(x[role+'_source']>0 for x in positive),
            'families_with_complete_role_set':sum(x[role+'_complete'] for x in positive),
            'families_with_any_retrieved':sum(x[role+'_retrieved']>0 for x in positive)})
    ab_summary=[]
    for lv in ['microseconds','seconds','day']:
        rs=[r for r in ablations if r['level']==lv]
        ab_summary.append({'timestamp_precision':lv,'unique_matches':sum(r['unique'] for r in rs),'ambiguous':sum(r['candidates']>1 for r in rs),'no_candidate':sum(r['candidates']==0 for r in rs),'same_as_fine':sum(r['same_as_fine'] for r in rs), 'ambiguous_among_458_fine_matches':sum(r['fine_matched'] and r['candidates']>1 for r in rs)})
    counts=collections.Counter((r['source_role'],r['heading_only_role']) for r in rows if r['status']=='matched')
    heading=[{'source_role':a,'heading_role':b or 'abstain','rows':n} for (a,b),n in sorted(counts.items())]
    summary={'sampling_exactly_reproduced':True,'raw_files_read':len(raw_hashes),'sample_families':len(families),'positive_sample_families':len(positive),
        'eligible_rows':len(rows),'matched':sum(r['status']=='matched' for r in rows),
        'distinct_source_DOIs':len({r['source_doi'] for r in rows if r['source_doi']}),
        'unique_report_objects':len({r['source_doi'] for r in rows if r['source_role']=='review-article'}),
        'source_objects_positive':sum(sum(x[r+'_source'] for r in roles) for x in positive),
        'all_source_roles_complete_families':sum(x['all_roles_complete'] for x in positive),
        'heading_baseline':{'correct_rows':sum(r['heading_correct'] for r in rows),'wrong_rows':sum(r['heading_wrong'] for r in rows),
            'matched_abstentions':sum(r['status']=='matched' and not r['heading_only_role'] for r in rows)},
        'boundary':'New diagnostics are exploratory, conditional on publisher source identity, and not independent human validation or population rates.'}
    for name,data in [('independent_row_check',rows),('family_role_coverage',families),('role_coverage_summary',role_summary),('timestamp_ablation',ab_summary),('heading_baseline',heading)]:writecsv(out/(name+'.csv'),data)
    # A favorable assignment gives each unresolved endpoint row one previously
    # unmatched source object. This is a bound, not a new matching decision.
    unresolved=sum(r['status']=='unresolved' for r in rows)
    bounds=[]
    unresolved_by_family=collections.Counter(r['family'] for r in rows if r['status']=='unresolved')
    for row in role_summary:
        bounds.append({'role':row['role'], 'source_objects':row['source_objects'],
            'unique_matched_objects':row['retrieved_objects'],
            'not_uniquely_reconstructed':row['unmatched_objects'],
            'unrepresented_lower_bound':max(0,row['unmatched_objects']-unresolved),
            'all_unresolved_allocated_favorably':unresolved,
            'family_respecting_lower_bound':sum(max(0,x[row['role']+'_missing']-unresolved_by_family[x['family']]) for x in positive)})
    bounds.append({'role':'all_roles','source_objects':471,'unique_matched_objects':362,
        'not_uniquely_reconstructed':109,'unrepresented_lower_bound':105,
        'all_unresolved_allocated_favorably':unresolved,
        'family_respecting_lower_bound':sum(max(0,sum(x[r+'_missing'] for r in roles)-unresolved_by_family[x['family']]) for x in positive)})
    writecsv(out/'coverage_bounds.csv',bounds)
    # Independent complete residual census reconstruction, including admissible
    # identifier checks. Do not count source-oracle identifiers as deployable gains.
    base=repo/'Evidence/Provenance'
    manifest=read(base/'exception_census/manifest.json')
    links={str(x['family']):x for x in read(repo/'Inputs/records/Results/epmc_family_links.json')}
    routes={str(x['family']):x for x in read(repo/'Inputs/records/Results/epmc_route_details.json')}
    records=read(repo/'Inputs/records/Results/epmc_route_records.json')
    residual=[]
    for cap in manifest:
        f=str(cap['family']);raw=(base/cap['path']).read_bytes()
        assert cap['status']==200 and hashlib.sha256(raw).hexdigest()==cap['sha256']
        objects=json.loads(raw);query=normdoi(cap['doi']);positive=False;text=False
        for obj in objects:
            dm=obj.get('docmap',obj)
            if str((dm.get('publisher') or {}).get('name','')).casefold()!='elife':continue
            for step in dm.get('steps',{}).values():
                if query not in {normdoi(v.get('doi')) for v in step.get('inputs',[])}:continue
                for action in step.get('actions',[]):
                    for value in action.get('outputs',[]):
                        if value.get('type') in {'review','reviews-summary','referee-report','review-article'}:
                            positive=True
                            text=text or any(bool(c.get('text')) for c in value.get('content',[]))
        natives={normdoi(records.get(k,{}).get('doi')) for k in routes[f]['native_preprint_targets']}
        crossrefs={normdoi(v) for v in routes[f]['source_concordant_preprint_DOIs']}
        # The specific queried DOI, rather than just some DOI in the family,
        # must be available on a declared operational route.
        declared=query in natives|crossrefs
        residual.append({'family':f,'stratum':cap['stratum'],'input_doi':query,
            'upstream_review':int(positive),'upstream_text':int(text),
            'declared_input_available':int(declared),
            'operational_gain':int(positive and declared)})
    assert len(residual)==len({x['family'] for x in residual})==647
    assert sum(x['upstream_review'] for x in residual)==512
    assert sum(x['operational_gain'] for x in residual)==509
    assert all(x['upstream_text'] for x in residual if x['operational_gain'])
    writecsv(out/'residual_census_check.csv',residual)
    summary['independent_residual_census']={'frozen_responses_verified':647,
        'review_positive':512,'operational_gain_with_declared_input':509,
        'source_only_positives_excluded':3,'review_presence_total':3409,
        'population_denominator':3547}
    (out/'independent_results.json').write_text(json.dumps(summary,indent=2)+'\n')
    print(json.dumps(summary,indent=2));print('ROLES',role_summary);print('ABLATIONS',ab_summary);print('HEADINGS',heading)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--repo',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args();main(a.repo,a.out)
