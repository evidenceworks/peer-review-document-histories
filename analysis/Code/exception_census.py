"""Inspect upstream EEB events for every unmet role-qualified retrieval task.

Retains completed responses and exceptions. Does not infer an ingestion cause.
"""
import argparse,collections,concurrent.futures,csv,datetime,hashlib,json,time,urllib.request,urllib.error
from pathlib import Path

def atomic(path,value):
 tmp=path.with_suffix(path.suffix+'.tmp');tmp.write_text(json.dumps(value,indent=2)+'\n');tmp.replace(path)

def run():
 ap=argparse.ArgumentParser();ap.add_argument('--audit',type=Path,default=Path(__file__).resolve().parent);ap.add_argument('--inputs',type=Path,default=Path(__file__).resolve().parents[2]/'inputs/v41/Results');ap.add_argument('--offline',action='store_true');a=ap.parse_args();root=a.audit;out=root/'exception_census';out.mkdir(exist_ok=True);raw=out/'raw';raw.mkdir(exist_ok=True)
 fams=list(csv.DictReader((root/'endpoint_family_ledger.csv').open()));all_fams=fams;fams=[r for r in fams if r['has_vor']=='1' and r['exact_MED']=='1' and r['enriched_review']=='0'];links={r['family']:r for r in json.loads((a.inputs/'epmc_family_links.json').read_text())};routes={r['family']:r for r in json.loads((a.inputs/'epmc_route_details.json').read_text())}
 targets=[]
 for r in sorted(fams,key=lambda x:x['family']):
  for i,doi in enumerate(links[r['family']]['original_preprint_dois']):
   key=r['family']+'_'+str(i);cache=root/'validation/raw/eeb'/(key+'.json');path=cache if cache.exists() else raw/(key+'.json')
   # Reuse the two separately labeled diagnostic probes only for byte-identical URLs.
   probe=root/'probes'/('eeb_'+r['family']+'.json')
   if not path.exists() and probe.exists() and i==0:path=probe
   targets.append({'family':r['family'],'stratum':r['residual_class'],'doi':doi,'key':key,'url':'https://eeb.embo.org/api/v2/docmap/'+doi,'path':str(path.relative_to(root)),'cached_before_census':path.exists()})
 plan={'created_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'scope':'All source-VOR exact-MED families lacking a review-typed eLife endpoint event under the enriched declared route. Fixed census extension after stratified mechanism audit; not preregistered.','family_n':len(fams),'family_strata':dict(collections.Counter(r['residual_class'] for r in fams)),'rule':'Presence of an explicit eLife publisher (case-insensitive) and output type review, reviews-summary, referee-report or review-article, bound to the declared original preprint input DOI. Publication-only and author-response-only outputs do not meet that type rule. An EEB event is not proof of current consensus or complete history.','targets':targets}
 if not (out/'plan.json').exists():atomic(out/'plan.json',plan)
 else:
  old=json.loads((out/'plan.json').read_text());assert [(r['family'],r['doi'])for r in old['targets']]==[(r['family'],r['doi'])for r in targets];plan=old;targets=plan['targets']
 print('Frozen census',len(fams),'families',len(targets),'queries',sum(r['cached_before_census'] for r in targets),'cached; plan SHA',hashlib.sha256((out/'plan.json').read_bytes()).hexdigest(),flush=True)
 def fetch(t):
  p=root/t['path'];rec=p.with_suffix('.receipt.json')
  if p.exists() and rec.exists():
   old=json.loads(rec.read_text());assert old['sha256']==hashlib.sha256(p.read_bytes()).hexdigest();return {**t,'status':old.get('status',old.get('http_status',200)),'sha256':old['sha256'],'bytes':len(p.read_bytes()),'capture_utc':old.get('capture_utc',old.get('captured_utc')),'reused':True}
  if a.offline:raise FileNotFoundError(p)
  receipt=dict(t);receipt['reused']=False
  for i in range(3):
   try:
    with urllib.request.urlopen(urllib.request.Request(t['url'],headers={'User-Agent':'aOIR-provenance-exception-audit/4.2'}),timeout=40) as r:b=r.read();status=r.status
    json.loads(b);p.write_bytes(b);receipt.update(status=status,sha256=hashlib.sha256(b).hexdigest(),bytes=len(b),capture_utc=datetime.datetime.now(datetime.timezone.utc).isoformat());break
   except urllib.error.HTTPError as e:
    receipt.update(status=e.code,error=str(e))
    if e.code not in [429,500,502,503,504]:break
    time.sleep(2+i)
   except Exception as e:receipt.update(status='failed',error=repr(e));time.sleep(2+i)
  atomic(rec,receipt);return receipt
 if a.offline:
  manifest=json.loads((out/'manifest.json').read_text())
  for t in manifest:
   if t['status']==200:assert hashlib.sha256((root/t['path']).read_bytes()).hexdigest()==t['sha256']
 else:
  manifest=[]
  with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
   for i,r in enumerate(pool.map(fetch,targets),1):
    manifest.append(r)
    if i%30==0:atomic(out/'manifest.json',manifest);print(i,'/',len(targets),'HTTP200',sum(x['status']==200 for x in manifest),flush=True)
  atomic(out/'manifest.json',manifest)
 byfam=collections.defaultdict(list)
 for x in manifest:byfam[x['family']].append(x)
 records=json.loads((a.inputs/'epmc_route_records.json').read_text());fallback=[]
 rows=[];events=[]
 for f in sorted(fams,key=lambda x:x['family']):
  fid=f['family'];caps=byfam[fid];rr=[];unbound=0;mapids=[]
  for cap in caps:
   if cap['status']!=200:continue
   d=json.loads((root/cap['path']).read_text())
   for di,z in enumerate(d):
    dm=z.get('docmap',z);publisher=(dm.get('publisher')or{}).get('name','');mapids.append(dm.get('id'))
    for si,st in dm.get('steps',{}).items():
     ins=[(i.get('doi')or'').lower() for i in st.get('inputs',[])];bound=cap['doi'].lower() in ins
     for ai,act in enumerate(st.get('actions',[])):
      for oi,o in enumerate(act.get('outputs',[])):
       row={'family':fid,'query_doi':cap['doi'],'publisher':publisher,'type':o.get('type') or '', 'output_doi':o.get('doi') or '', 'date':o.get('published')or'', 'input_DOI_concordant':int(bound),'uri':o.get('uri') or '', 'url':o.get('url')or'','content_web_urls':'|'.join(c.get('url')or'' for c in o.get('content',[]) if c.get('url')),'has_text':int(any(c.get('text') for c in o.get('content',[]))),'docmap_id':dm.get('id')or'','source_sha256':cap['sha256']}
       rr.append(row);events.append(row)
  bound=[r for r in rr if r['input_DOI_concordant']];er=[r for r in bound if r['publisher'].casefold()=='elife'];ev=[r for r in er if r['type'] in {'review','reviews-summary','referee-report','review-article'}]
  if any(c['status']!=200 for c in caps):outcome='request_error'
  elif ev:outcome='elife_review_event_present'
  elif er:outcome='elife_event_without_review_type'
  elif bound:outcome='other_publisher_events_only'
  elif rr:outcome='events_without_input_DOI_concordance'
  else:outcome='no_event_outputs'
  declared_native={str(records.get(k,{}).get('doi') or '').lower() for k in routes[fid]['native_preprint_targets']}
  declared_crossref=set(routes[fid]['source_concordant_preprint_DOIs']);known_original=set(links[fid]['original_preprint_dois']);positive=bool(ev)
  fallback.append({'family':fid,'upstream_review':int(positive),'Crossref_input_available':int(bool(known_original&declared_crossref)),'native_PPR_input_available':int(bool(known_original&declared_native)),'eligible_declared_EEB_fallback':int(positive and bool(known_original&(declared_crossref|declared_native)))})
  rows.append({'family':fid,'endpoint_class':f['residual_class'],'original_preprint_doi':'|'.join(links[fid]['original_preprint_dois']),'original_DOI_declared_by_Crossref':int(bool(routes[fid]['source_concordant_preprint_DOIs'])),'response_n':len(caps),'all_HTTP200':int(all(c['status']==200 for c in caps)),'output_event_n':len(rr),'elife_event_n':len(er),'elife_review_event_n':len(ev),'elife_review_event_with_text_n':sum(r['has_text'] for r in ev),'elife_publication_event_n':sum(r['type']=='journal-publication' for r in er),'elife_output_types':'|'.join(sorted({r['type']for r in er})),'other_publisher_names':'|'.join(sorted({r['publisher']for r in bound if r['publisher'].casefold()!='elife'})),'upstream_outcome':outcome,'duplicate_docmap_id_n':len(mapids)-len(set(mapids))})
 for name,data in [('family_ledger.csv',rows),('event_ledger.csv',events),('fallback_eligibility.csv',fallback)]:
  with (out/name).open('w',newline='')as fp:
   w=csv.DictWriter(fp,fieldnames=list(data[0]));w.writeheader();w.writerows(data)
 summary={'plan_sha256':hashlib.sha256((out/'plan.json').read_bytes()).hexdigest(),'families':len(rows),'responses':len(manifest),'http200':sum(r['status']==200 for r in manifest),'raw_bytes':sum(r.get('bytes',0)for r in manifest),'outcome_counts':dict(collections.Counter(r['upstream_outcome']for r in rows)),'by_endpoint_class':{cl:{'n':len(ss),'upstream_outcomes':dict(collections.Counter(r['upstream_outcome']for r in ss)),'source_original_DOI_declared_by_Crossref':sum(r['original_DOI_declared_by_Crossref']for r in ss),'elife_review_events':sum(r['elife_review_event_n']for r in ss),'elife_review_with_text_families':sum(r['elife_review_event_with_text_n']>0 for r in ss)} for cl in sorted({r['endpoint_class']for r in rows})for ss in [[r for r in rows if r['endpoint_class']==cl]]},'errors':[r for r in manifest if r['status']!=200],'scope':'A census of unmet endpoint tasks at the archived capture compared with publicly returned EEB events. Exact input DOI and event type are checked. Differences locate an observed boundary, not a causal software fault or lack of any public review.'}
 eligible=[r for r in all_fams if r['has_vor']=='1' and r['exact_MED']=='1'];base=sum(int(r['enriched_review'])for r in eligible)
 fallback_summary={'upstream_review_n':sum(r['upstream_review']for r in fallback),'strict_Crossref_eligible_recovery_n':sum(r['upstream_review']*r['Crossref_input_available']for r in fallback),'declared_Crossref_or_native_PPR_eligible_recovery_n':sum(r['eligible_declared_EEB_fallback']for r in fallback),'not_input_reachable_positive':[r['family']for r in fallback if r['upstream_review']and not r['eligible_declared_EEB_fallback']],'combined_review_presence_n':base+sum(r['eligible_declared_EEB_fallback']for r in fallback),'denominator':len(eligible),'scope':'Post hoc explicit upstream fallback from a DOI already declared in Crossref or a native PPR record. Source-concordant original DOI validates linkage; no title matching. EEB review-type and text presence are not individual report provenance or complete history.'}
 atomic(out/'fallback_summary.json',fallback_summary)
 atomic(out/'summary.json',summary);print(json.dumps(summary,indent=2),flush=True)
if __name__=='__main__':run()
