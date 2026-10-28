"""Audit the semantic content of archived Europe PMC evaluation responses.

This executable intentionally distinguishes provider presence, review-type event
presence, and a nonempty review destination. None measures a complete history.
"""
from __future__ import annotations
import argparse, collections, csv, hashlib, json, random, re
from pathlib import Path
from urllib.parse import urlsplit

REVIEW_TYPES = {'review', 'reviews-summary', 'referee-report', 'review-article'}
SEED=2026102442

def read(path):
    return json.loads(path.read_text(encoding='utf-8'))

def write_csv(path, rows):
    if not rows:
        path.write_text('')
        return
    with path.open('w', newline='', encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)

def is_elife(r):
    return (r.get('platform') or '').casefold()=='elife'

def event_kind(rows):
    return sorted({r.get('type') or '' for r in rows})

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--input',type=Path,required=True);ap.add_argument('--output',type=Path,required=True);ap.add_argument('--source-states',type=Path);a=ap.parse_args();a.output.mkdir(parents=True,exist_ok=True)
    ev=read(a.input/'epmc_evaluations.json');rec=read(a.input/'epmc_route_records.json');dt={r['family']:r for r in read(a.input/'epmc_route_details.json')};links={r['family']:r for r in read(a.input/'epmc_family_links.json')};target={r['family']:r for r in read(a.input/'crossref_source_targets.json')}
    with (a.input/'epmc_route_ledger.csv').open() as f: old=list(csv.DictReader(f))
    if a.source_states:
        initial={r['family']:r['group'] for r in read(a.source_states) if r['role']=='preprint' and r['file_version']==1}
        assert len(initial)==len(old)
        for row in old: row['initial_group']=initial[row['family']]
    all_rows=[];endpoint=[]
    for key,rows in sorted(ev.items()):
        er=[r for r in rows if is_elife(r)];rr=[r for r in er if r.get('type') in REVIEW_TYPES]
        urls=sorted({r['url'] for r in rr if r.get('url')})
        rdoi=(rec.get(key,{}).get('doi') or '').lower()
        # Exact duplicates of retained semantic fields; no claim about unique reviews.
        semantic_keys=[tuple(str(r.get(k)) for k in ['doi','dataOrigin','platform','type','url','evaluationDate']) for r in er]
        endpoint.append({'record':key,'record_doi':rdoi,'rows':len(rows),'elife_rows':len(er),'elife_review_rows':len(rr),'elife_publication_rows':sum(r.get('type')=='journal-publication' for r in er),'review_url_rows':sum(bool(r.get('url')) for r in rr),'review_null_url_rows':sum(not r.get('url') for r in rr),'review_distinct_urls':len(urls),'review_distinct_dates':len({r.get('evaluationDate') for r in rr if r.get('evaluationDate')}),'semantic_repeat_excess':len(semantic_keys)-len(set(semantic_keys)),'review_doi_matches_record':sum((r.get('doi')or'').lower()==rdoi for r in rr),'review_doi_differs_record':sum((r.get('doi')or'').lower()!=rdoi for r in rr),'types':'|'.join(event_kind(er)),'review_urls':'|'.join(urls),'unknown_platform_rows':sum((r.get('platform')or'').startswith('[Ljava.') for r in rows)})
        for j,r in enumerate(rows):
            all_rows.append({'record':key,'row_index':j,**r,'elife':int(is_elife(r)),'review_typed':int(is_elife(r) and r.get('type') in REVIEW_TYPES),'url_host':urlsplit(r.get('url') or '').netloc})
    families=[]
    for x in old:
        f=x['family'];d=dt[f];row={'family':f,'has_vor':int(x['has_vor']),'exact_MED':int(x['exact_MED']),'entry_year':x['entry_year'],'initial_group':x['initial_group'],'current_vor_group':x['current_vor_group'],'first_vor_age_days':x['first_vor_age_days'],'original_preprint_dois':'|'.join(links[f]['original_preprint_dois']),'native_targets':'|'.join(d['native_preprint_targets']),'bridge_targets':'|'.join(d['strict_bridge_PPR_targets'])}
        pathsets={'native':set(d['native_preprint_targets']),'bridge':set(d['strict_bridge_PPR_targets']),'enriched':set(d['native_preprint_targets'])|set(d['strict_bridge_PPR_targets'])}
        for route,keys in pathsets.items():
            er=[r for key in keys for r in ev.get(key,[]) if is_elife(r)];rr=[r for r in er if r.get('type') in REVIEW_TYPES]
            row[route+'_provider']=int(bool(er));row[route+'_review']=int(bool(rr));row[route+'_review_url']=int(any(r.get('url') for r in rr));row[route+'_publication_only']=int(bool(er) and not rr and all(r.get('type')=='journal-publication' for r in er));row[route+'_elife_rows']=len(er);row[route+'_review_rows']=len(rr)
        keys=pathsets['enriched'];er=[r for key in keys for r in ev.get(key,[]) if is_elife(r)];rr=[r for r in er if r.get('type') in REVIEW_TYPES]
        doiurls=[r['url'] for r in rr if r.get('url') and urlsplit(r['url']).hostname=='doi.org'];generic=[r['url'] for r in rr if r.get('url') and urlsplit(r['url']).hostname=='biorxiv.org']
        if not keys:cl='no_reachable_PPR'
        elif not er:cl='reachable_PPR_no_elife_event'
        elif row['enriched_publication_only']:cl='publication_only'
        elif not row['enriched_review']:cl='other_event_only'
        elif not row['enriched_review_url']:cl='review_no_url'
        elif doiurls:cl='review_with_DOI_url'
        elif generic:cl='review_with_generic_url'
        else:cl='review_other_url'
        row.update({'residual_class':cl,'review_gain':row['enriched_review']-row['native_review'],'review_url_gain':row['enriched_review_url']-row['native_review_url'],'provider_gain':row['enriched_provider']-row['native_provider'],'review_types':'|'.join(event_kind(rr)),'review_distinct_url_count':len({r['url'] for r in rr if r.get('url')}),'review_doi_url_count':len(set(doiurls)),'review_generic_url_count':len(set(generic)),'source_current_consensus_doi':target.get(f,{}).get('assessment_doi',''),'current_consensus_directly_identified':int(any(target.get(f,{}).get('assessment_doi','').lower() in (r.get('url')or'').lower() for r in rr)) if f in target else 0})
        assert row['native_provider']==int(x['native_elife_evaluation'])
        assert row['enriched_provider']==int(x['native_or_strict_bridge_elife_evaluation'])
        families.append(row)
    write_csv(a.output/'endpoint_family_ledger.csv',families);write_csv(a.output/'endpoint_record_ledger.csv',endpoint)
    # Row ledger permits exact traceability while avoiding duplication of full input captures.
    write_csv(a.output/'endpoint_row_ledger.csv',all_rows)
    summaries=[]
    for pop,rows in [('all_families',families),('VOR',[r for r in families if r['has_vor']]),('VOR_exact_MED',[r for r in families if r['has_vor'] and r['exact_MED']]),('VOR_age90',[r for r in families if r['has_vor'] and r['first_vor_age_days'] and int(r['first_vor_age_days'])>=90])]:
        for strat in ['all','current_vor_group','initial_group','entry_year']:
            for v in ['all'] if strat=='all' else sorted({r[strat] for r in rows}):
                ss=rows if strat=='all' else [r for r in rows if r[strat]==v]
                nums=['native_provider','native_review','native_review_url','enriched_provider','enriched_review','enriched_review_url','native_publication_only','enriched_publication_only','provider_gain','review_gain','review_url_gain','current_consensus_directly_identified']
                summaries.append({'population':pop,'stratifier':strat,'value':v,'n':len(ss),**{k:sum(r[k] for r in ss) for k in nums}})
    write_csv(a.output/'endpoint_route_summary.csv',summaries)
    focal=[r for r in families if r['has_vor'] and r['exact_MED']]
    rs=[r for rows in ev.values() for r in rows if is_elife(r)]
    classes=collections.Counter(r['residual_class'] for r in focal)
    report={'definition':'Provider presence is not evidence of a peer-review event; review-type presence is not a complete history or current consensus. A nonempty URL is not a validated content retrieval.','review_types':sorted(REVIEW_TYPES),'focal_n':len(focal),'endpoint_n':len(ev),'endpoint_elife_n':sum(bool(r['elife_rows']) for r in endpoint),'elife_rows':len(rs),'elife_type_counts':dict(collections.Counter(r.get('type') for r in rs)),'elife_url_hosts':dict(collections.Counter(urlsplit(r.get('url') or '').netloc or 'NULL' for r in rs)),'elife_missing_dates_by_type':dict(collections.Counter(r.get('type') for r in rs if not r.get('evaluationDate'))),'main':next(r for r in summaries if r['population']=='VOR_exact_MED' and r['stratifier']=='all'),'family_classes':dict(classes),'review_internal_doi_matches_preprint':sum(r['review_doi_matches_record'] for r in endpoint),'review_internal_doi_differs_preprint':sum(r['review_doi_differs_record'] for r in endpoint),'semantic_repeat_excess':sum(r['semantic_repeat_excess'] for r in endpoint),'input_hashes':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in [a.input/n for n in ['epmc_evaluations.json','epmc_route_records.json','epmc_route_details.json','epmc_family_links.json','crossref_source_targets.json','epmc_route_ledger.csv']]}}
    if a.source_states:
        report['input_hashes']['corrected_source_states.json']=hashlib.sha256(a.source_states.read_bytes()).hexdigest()
    (a.output/'endpoint_audit_summary.json').write_text(json.dumps(report,indent=2)+'\n')
    rng=random.Random(SEED);sample=[]
    for cl in sorted(classes):
        frame=sorted([r for r in focal if r['residual_class']==cl],key=lambda r:r['family'])
        for r in rng.sample(frame,min(20,len(frame))):
            sample.append({'family':r['family'],'stratum':cl,'stratum_N':len(frame),'stratum_n':min(20,len(frame)),'endpoints':sorted(set(r['native_targets'].split('|')+r['bridge_targets'].split('|'))-{''}),'original_preprint_dois':links[r['family']]['original_preprint_dois']})
    plan={'label':'Exploratory V4.2 stratified audit, selected after endpoint classes were observed and before new raw captures. No claim of preregistration.','seed':SEED,'population':'3,547 source-VOR families with exact Europe PMC MED record','sampling':'20 family simple random draws per disjoint observable endpoint stratum, or census if smaller. No replacement. No inferential pooling across unequal fractions.','sample':sample}
    (a.output/'raw_validation_plan.json').write_text(json.dumps(plan,indent=2)+'\n')
    print(json.dumps(report,indent=2));print('validation_sample_n',len(sample))

if __name__=='__main__':main()
