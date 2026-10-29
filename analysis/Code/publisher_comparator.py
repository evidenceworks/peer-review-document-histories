"""Source-only current VOR assessment comparator for the same frozen sample."""
import argparse,collections,concurrent.futures,csv,datetime,hashlib,json,time,urllib.request
from pathlib import Path
ROOT=Path(__file__).resolve().parent

def main():
 global ROOT
 ap=argparse.ArgumentParser();ap.add_argument('--audit',type=Path,default=ROOT);ap.add_argument('--inputs',type=Path,default=ROOT.parent.parent/'inputs/records/Results');ap.add_argument('--offline',action='store_true');args=ap.parse_args();ROOT=args.audit
 plan=json.loads((ROOT/'raw_validation_plan.json').read_text());dest=ROOT/'validation/raw/publisher_versions';dest.mkdir(parents=True,exist_ok=True)
 amend={'purpose':'Exploratory comparator using official publisher versions endpoint for the SAME frozen 120-family sample. Added after matched-pipeline results were known. No new family selection.','selector':'Filter status=vor; choose greatest integer production version, breaking by versionDate; require unique record and elifeAssessment DOI. Use its strength set, never previous VOR when current lacks assessment. Score against frozen JATS only afterward.','known_probe':'family100000 (and root87297); not aggregate sample evidence','plan_sha256':hashlib.sha256((ROOT/'raw_validation_plan.json').read_bytes()).hexdigest(),'created_utc':datetime.datetime.now(datetime.timezone.utc).isoformat()}
 if not (ROOT/'validation/publisher_comparator_amendment.json').exists():
  (ROOT/'validation/publisher_comparator_amendment.json').write_text(json.dumps(amend,indent=2)+'\n')
 def one(x):
  f=x['family'];url=f'https://api.elifesciences.org/articles/{f}/versions';p=dest/(f+'.json');rp=p.with_suffix('.receipt.json')
  if p.exists() and rp.exists():
   previous=json.loads(rp.read_text());assert hashlib.sha256(p.read_bytes()).hexdigest()==previous['sha256'];return previous
  if args.offline:raise FileNotFoundError(p)
  receipt={'family':f,'url':url}
  for i in range(3):
   try:
    with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'peer-review-history-research/1.0'}),timeout=40) as r:b=r.read();s=r.status
    json.loads(b);p.write_bytes(b);receipt.update(status=s,bytes=len(b),sha256=hashlib.sha256(b).hexdigest(),capture_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),path=str(p));break
   except Exception as e:
    receipt.update(status='error',error=repr(e));time.sleep(1+i)
  rp.write_text(json.dumps(receipt,indent=2)+'\n');return receipt
 manifest=[]
 with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
  for i,r in enumerate(pool.map(one,plan['sample']),1):
   manifest.append(r)
   if i%30==0:
    temp=ROOT/'validation/publisher_comparator_manifest.tmp';temp.write_text(json.dumps(manifest,indent=2)+'\n');temp.replace(ROOT/'validation/publisher_comparator_manifest.json')
   if i%30==0:print(i,'/',len(plan['sample']),flush=True)
 temp=ROOT/'validation/publisher_comparator_manifest.tmp';temp.write_text(json.dumps(manifest,indent=2)+'\n');temp.replace(ROOT/'validation/publisher_comparator_manifest.json')
 gold={x['family']:x for x in json.loads((args.inputs/'crossref_source_targets.json').read_text())};rows=[]
 for x,m in zip(plan['sample'],manifest):
  f=x['family'];r={'family':f,'stratum':x['stratum'],'status':m['status'],'selected_production_version':'','selected_date':'','selected_assessment_doi':'','selected_strength':'','gold_assessment_doi':gold[f]['assessment_doi'],'gold_labels':'|'.join(sorted(gold[f]['labels'])),'doi_concordance':0,'strength_concordance':0,'abstained':1}
  if m['status']==200:
   d=json.loads((dest/(f+'.json')).read_text());vors=[v for v in d.get('versions',[]) if v.get('status')=='vor']
   if vors:
    key=lambda v:(int(v.get('version',-1)),v.get('versionDate') or '')
    highest=max(map(key,vors));selected=[v for v in vors if key(v)==highest]
    if len(selected)==1:
     v=selected[0];ev=v.get('elifeAssessment')or{};doi=(ev.get('doi')or'').lower();strength={s.lower() for s in ev.get('strength',[])}
     r.update(selected_production_version=v.get('version'),selected_date=v.get('versionDate'),selected_assessment_doi=doi,selected_strength='|'.join(sorted(strength)),doi_concordance=int(doi==gold[f]['assessment_doi']),strength_concordance=int(strength==set(gold[f]['labels'])),abstained=int(not doi))
  rows.append(r)
 with (ROOT/'publisher_comparator.csv').open('w',newline='') as f:
  w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
 summary={'n':len(rows),'captured':sum(r['status']==200 for r in rows),'abstained':sum(r['abstained'] for r in rows),'DOI_concordant':sum(r['doi_concordance'] for r in rows),'strength_set_concordant':sum(r['strength_concordance'] for r in rows),'discordances':[r for r in rows if not (r['doi_concordance'] and r['strength_concordance'])],'scope':'Publisher API comparator, same institutional source as JATS; not independent adjudication or population-wide estimate. Live capture vs frozen reference; selector did not use reference DOI or grade.'}
 (ROOT/'publisher_comparator_summary.json').write_text(json.dumps(summary,indent=2)+'\n');print(json.dumps(summary,indent=2))
if __name__=='__main__':main()
