"""Conservative timestamp-assisted role recovery in the frozen audit sample.

A row is repaired only when its preprint DOI is a declared source input and its
precise date string uniquely matches an identified source review-related output.
No source assessment grades or gold current DOI participate in matching.
"""
from __future__ import annotations
import argparse,collections,csv,datetime,hashlib,json,re
from pathlib import Path
ROOT=Path(__file__).resolve().parent
ROLE_TYPES={'review-article','evaluation-summary','reply'}
FIELDS=['id','doi','dataOrigin','platform','type','url','evaluationDate','dateUpdated']

def load(p):return json.loads(p.read_text(encoding='utf-8'))
def normdoi(s):return re.sub(r'^https?://(?:dx\.)?doi\.org/','',(s or '').strip(),flags=re.I).lower().rstrip('/')
def stamp(s):
    if not s or not re.search(r'[T ]\d{2}:\d{2}:\d{2}',s):return None
    try:
        d=datetime.datetime.fromisoformat(s.replace('Z','+00:00'))
        if d.tzinfo:d=d.astimezone(datetime.timezone.utc).replace(tzinfo=None)
        return d.isoformat(timespec='microseconds')
    except (ValueError,TypeError):return None

def outcsv(path,rows):
    with path.open('w',newline='',encoding='utf-8') as f:
        if rows:
            w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)

def source_outputs(d,fam):
    collected={};external=set();targets=collections.defaultdict(set)
    for k,step in d.get('steps',{}).items():
        inputs=step.get('inputs',[])
        ext=[i for i in inputs if i.get('doi') and not normdoi(i['doi']).startswith('10.7554/elife.')]
        external|={normdoi(i['doi']) for i in ext}
        inputs_ids={normdoi(i.get('doi')) for i in inputs if i.get('doi')}
        outputs=[o for a in step.get('actions',[]) for o in a.get('outputs',[])]
        participants={id(o):a.get('participants',[]) for a in step.get('actions',[]) for o in a.get('outputs',[])}
        # A publishing step explicitly uses evaluation objects to produce a container.
        # This is not itself a claim that the output container was the evaluated input.
        for o in outputs:
            if o.get('type')=='preprint' and o.get('doi'):
                for ident in inputs_ids:targets[ident].add(normdoi(o['doi']))
        for o in outputs:
            if o.get('type') not in ROLE_TYPES:continue
            ident=normdoi(o.get('doi'));dt=stamp(o.get('published'))
            if not ident:continue
            key=(ident,o.get('type'),dt)
            if key not in collected:
                collected[key]={'family':fam,'source_doi':ident,'source_type':o['type'],'source_date':dt,'source_url':o.get('url'),'source_step':k,'declared_inputs':[{'doi':normdoi(i['doi']),'versionIdentifier':i.get('versionIdentifier'),'url':i.get('url')} for i in ext],'declared_input_dois':sorted({normdoi(i['doi']) for i in ext}),'declared_input_versions':sorted({str(i.get('versionIdentifier','')) for i in ext}),'declared_input_urls':sorted({i.get('url','') for i in ext}),'content_urls':sorted({c.get('url','') for c in o.get('content',[]) if c.get('url')}),'participant_roles':sorted({q.get('role','') for q in participants.get(id(o),[])}),'participant_names':sorted({(q.get('actor')or{}).get('name','') for q in participants.get(id(o),[])})}
    for r in collected.values():r['reviewed_preprint_containers']=sorted(targets[r['source_doi']])
    return list(collected.values()),external

def eeb_outputs(maps,fam):
    rows=[]
    for mi,obj in enumerate(maps):
        dm=obj.get('docmap',obj);pub=(dm.get('publisher')or{}).get('name','')
        for k,step in dm.get('steps',{}).items():
            for ai,a in enumerate(step.get('actions',[])):
                for oi,o in enumerate(a.get('outputs',[])):
                    content=o.get('content',[]);text='\n'.join(c.get('text','') for c in content if c.get('text'))
                    rows.append({'family':fam,'map_index':mi,'docmap_id':dm.get('id'),'publisher':pub,'type':o.get('type'),'doi':normdoi(o.get('doi')),'uri':o.get('uri'),'url':o.get('url'),'published':stamp(o.get('published')),'content_urls':[c.get('url') for c in content if c.get('url')],'text':text,'text_sha256':hashlib.sha256(text.encode()).hexdigest() if text else None,'input_dois':[normdoi(i.get('doi')) for i in step.get('inputs',[]) if i.get('doi')],'input_versions':[i.get('versionIdentifier') for i in step.get('inputs',[]) if i.get('versionIdentifier')],'eeb_step':k,'action_index':ai,'output_index':oi,'participant_roles':[q.get('role') for q in a.get('participants',[])],'participant_names':[(q.get('actor')or{}).get('name') for q in a.get('participants',[])]})
    return rows

def repair_decision(row, bydate):
    if row.get('type') not in {'review','reviews-summary'}:
        return 'not_review_type', [], None
    dt=stamp(row.get('evaluationDate'))
    if not dt:
        return 'missing_or_insufficient_date', [], None
    all_candidates=bydate.get(dt,[])
    doi=normdoi(row.get('doi'))
    candidates=[c for c in all_candidates if doi in c['declared_input_dois']]
    if all_candidates and not candidates:
        return 'source_input_DOI_mismatch', [], None
    if not candidates:
        return 'no_source_date_match', [], None
    if len(candidates)>1:
        return 'ambiguous_source_date', candidates, None
    return 'matched', candidates, candidates[0]

def main():
    global ROOT
    ap=argparse.ArgumentParser();ap.add_argument('--audit',type=Path,default=ROOT);ap.add_argument('--inputs',type=Path,default=ROOT.parent.parent/'inputs/records/Results');a=ap.parse_args();ROOT=a.audit
    plan=load(ROOT/'raw_validation_plan.json');manifest=load(ROOT/'validation/capture_manifest.json');archive=load(a.inputs/'epmc_evaluations.json');frozen_targets={r['family']:r for r in load(a.inputs/'crossref_source_targets.json')};gold=load(a.inputs/'assessment_source_targets.json')
    repairs=[];source_all=[];eeb_all=[];famrows=[];schema=collections.Counter();fullschema=set();exact_agree=0;epnum=0
    for p in plan['sample']:
        f=p['family'];sd=load(ROOT/'validation/raw/elife'/f'{f}.json');src,ext=source_outputs(sd,f);source_all+=src;bydate=collections.defaultdict(list)
        for x in src:
            if x['source_date']:bydate[x['source_date']].append(x)
        eeb=[]
        for i,doi in enumerate(p['original_preprint_dois']):eeb+=eeb_outputs(load(ROOT/'validation/raw/eeb'/f'{f}_{i}.json'),f)
        eeb_all+=eeb;ebdate=collections.defaultdict(list)
        for r in eeb:
            if r['publisher'].casefold()=='elife' and r['published']:ebdate[r['published']].append(r)
        ep=[];live_equal=True
        for key in p['endpoints']:
            d=load(ROOT/'validation/raw/epmc'/(key.replace('/','_')+'.json'));rr=d.get('evaluationList',{}).get('evaluation',[]);epnum+=1
            projected=[{k:r.get(k) for k in FIELDS} for r in rr]
            agrees=projected==archive[key];exact_agree+=agrees;live_equal &= agrees
            for r in rr:
                fullschema.update(r);schema.update(r.keys())
                if (r.get('platform')or'').casefold()=='elife':ep.append((key,r))
        repaired=[];failures=collections.Counter()
        for key,r in ep:
            dt=stamp(r.get('evaluationDate'));doi=normdoi(r.get('doi'))
            status,cs,matched=repair_decision(r,bydate)
            if matched:repaired.append(matched)
            failures[status]+=1;es=ebdate.get(dt,[]) if dt else []
            rr={'family':f,'stratum':p['stratum'],'epmc_record':key,'epmc_id':r.get('id'),'epmc_platform':r.get('platform'),'epmc_type':r.get('type'),'epmc_doi':doi,'epmc_date':dt or '','epmc_url':r.get('url') or '', 'epmc_title':r.get('title') or '', 'epmc_evaluators':json.dumps((r.get('evaluatorsList') or {}).get('evaluator',[]),sort_keys=True),'status':status,'source_candidate_n':len(cs),'source_doi':matched['source_doi'] if matched else '','source_type':matched['source_type'] if matched else '','source_input_versions':'|'.join(matched['declared_input_versions']) if matched else '', 'source_input_bindings':json.dumps(matched['declared_inputs'],sort_keys=True) if matched else '','source_reviewed_preprint_containers':'|'.join(matched['reviewed_preprint_containers']) if matched else '','eeb_same_date_outputs':len(es),'eeb_same_date_types':'|'.join(sorted({str(x['type']) for x in es})),'eeb_same_date_text_roles':'|'.join(sorted({('author_response' if re.search(r'author\s+response',x['text'][:150],re.I) else 'evaluation_summary' if re.search(r'elife\s+assessment',x['text'][:150],re.I) else 'reviewer_report' if re.search(r'reviewer\s*#',x['text'][:150],re.I) else 'unspecified') for x in es}))}
            repairs.append(rr)
        ids={r['source_doi'] for r in repaired};src_summary={r['source_doi'] for r in src if r['source_type']=='evaluation-summary'};ep_summary=ids&src_summary
        # Verify declared source DOIs against frozen references only AFTER retrieval.
        grades={q:gold[q]['labels'] for q in src_summary if q in gold};current_labels=frozen_targets[f]['labels'];retrieved_disagreement=[q for q in ep_summary if q in grades and set(grades[q])!=set(current_labels)]
        source_docmap_vors={normdoi(o.get('doi')) for st in sd.get('steps',{}).values() for ac in st.get('actions',[]) for o in ac.get('outputs',[]) if o.get('type')=='version-of-record'}
        famrows.append({'family':f,'stratum':p['stratum'],'stratum_N':p['stratum_N'],'stratum_n':p['stratum_n'],'epmc_endpoint_n':len(p['endpoints']),'live_projection_equals_archive':int(live_equal),'source_outputs':len(src),'source_reports':sum(r['source_type']=='review-article' for r in src),'source_summaries':len(src_summary),'source_replies':sum(r['source_type']=='reply' for r in src),'source_has_frozen_VOR_DOI':int(frozen_targets[f]['version_doi'] in source_docmap_vors),'eeb_elife_review_outputs':sum(r['publisher'].casefold()=='elife' and r['type']=='review' for r in eeb),'eeb_any_review_outputs':sum(r['type'] in ['review','reviews-summary'] for r in eeb),'eeb_elife_publication_outputs':sum(r['publisher'].casefold()=='elife' and r['type']=='journal-publication' for r in eeb),'epmc_elife_rows':len(ep),'epmc_elife_review_rows':sum(r.get('type') in {'review','reviews-summary'} for k,r in ep),'epmc_matched_rows':len(repaired),'epmc_unique_source_objects':len(ids),'epmc_unique_reports':len({r['source_doi'] for r in repaired if r['source_type']=='review-article'}),'epmc_unique_summaries':len(ep_summary),'epmc_unique_replies':len({r['source_doi'] for r in repaired if r['source_type']=='reply'}),'epmc_unmatched_review_rows':sum(v for k,v in failures.items() if k not in ['matched','not_review_type']),'source_summary_full_coverage':int(bool(src_summary) and ep_summary==src_summary),'retrieved_summary_set_disagrees_current_n':len(retrieved_disagreement),'retrieved_summary_set_disagrees_current_ids':'|'.join(sorted(retrieved_disagreement))})
    outcsv(ROOT/'matched_family_ledger.csv',famrows);outcsv(ROOT/'matched_row_repairs.csv',repairs)
    (ROOT/'matched_source_outputs.json').write_text(json.dumps(source_all,indent=2)+'\n');(ROOT/'matched_eeb_outputs.json').write_text(json.dumps(eeb_all,ensure_ascii=False,indent=2)+'\n')
    # Matched source-role by observed service-role transitions count both rows and unique objects.
    trans=[]
    for st in sorted({r['stratum'] for r in repairs}):
        for typ in sorted(ROLE_TYPES):
            rr=[r for r in repairs if r['stratum']==st and r['source_type']==typ];trans.append({'stratum':st,'source_type':typ,'epmc_type':'review','row_n':len(rr),'unique_source_objects':len({(r['family'],r['source_doi']) for r in rr}),'families':len({r['family'] for r in rr})})
    outcsv(ROOT/'matched_role_transitions.csv',trans)
    strata=[]
    for st in sorted({r['stratum'] for r in famrows}):
        ss=[r for r in famrows if r['stratum']==st]
        strata.append({'stratum':st,'N':ss[0]['stratum_N'],'sample_n':len(ss),'source_all_have_review_reports':int(all(r['source_reports']>0 for r in ss)),'eeb_review_present_n':sum(r['eeb_elife_review_outputs']>0 for r in ss),'epmc_review_present_n':sum(r['epmc_elife_review_rows']>0 for r in ss),'epmc_role_repaired_n':sum(r['epmc_matched_rows']>0 for r in ss),'epmc_author_reply_as_review_n':sum(r['epmc_unique_replies']>0 for r in ss),'epmc_summary_as_review_n':sum(r['epmc_unique_summaries']>0 for r in ss),'full_source_summary_coverage_n':sum(r['source_summary_full_coverage'] for r in ss),'retrieved_summary_disagrees_current_n':sum(r['retrieved_summary_set_disagrees_current_n']>0 for r in ss),'source_outputs':sum(r['source_outputs'] for r in ss),'epmc_matched_rows':sum(r['epmc_matched_rows'] for r in ss),'epmc_unique_source_objects':sum(r['epmc_unique_source_objects'] for r in ss)})
    outcsv(ROOT/'matched_stratum_summary.csv',strata)
    summary={'sample_families':len(famrows),'plan_sha256':hashlib.sha256((ROOT/'raw_validation_plan.json').read_bytes()).hexdigest(),'raw_captures':len(manifest),'raw_success':sum(r['status']==200 for r in manifest),'raw_bytes':sum(r.get('bytes',0) for r in manifest),'EPMC_endpoints_checked':epnum,'EPMC_projection_exactly_equal_archived':exact_agree,'raw_row_keys':sorted(fullschema),'epmc_row_status':dict(collections.Counter(r['status'] for r in repairs)),'source_role_counts_all':dict(collections.Counter(r['source_type'] for r in source_all)),'source_role_counts_matched_epmc_rows':dict(collections.Counter(r['source_type'] for r in repairs if r['status']=='matched')),'unique_matched_source_role_counts':dict(collections.Counter(typ for f,doi,typ in {(r['family'],r['source_doi'],r['source_type']) for r in repairs if r['status']=='matched'})),'strata':strata,'interpretation':'Exploratory stratified mechanism audit. Counts are sample facts; unequal fractions preclude unweighted population percentages. Matching is based on declared original DOI and exact publication date-time (explicit hours, minutes, and seconds required; finer digits retained), without gold labels or current consensus. A matching timestamp is evidence of linkage, not proof of identical full text. The evaluated input DOI and version come from the evaluation step. Separately, reviewed-preprint containers come from downstream publishing steps. Container association does not prove the evaluation targets a VOR or another version.'}
    (ROOT/'matched_provenance_summary.json').write_text(json.dumps(summary,indent=2)+'\n');print(json.dumps(summary,indent=2))
if __name__=='__main__':main()
