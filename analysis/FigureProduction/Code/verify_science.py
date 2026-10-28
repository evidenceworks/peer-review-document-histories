"""Independent numerical audit. Does not import the figure-producing program.
Reconstructs original/seconds/day candidate sets from object inputs and timestamps.
"""
from pathlib import Path
from collections import Counter,defaultdict
from datetime import datetime,timezone
import csv,json,argparse,hashlib,xml.etree.ElementTree as ET
p=argparse.ArgumentParser(description=__doc__);p.add_argument('--figures',type=Path,required=True);p.add_argument('--report',type=Path,required=True);a=p.parse_args()
R=Path(__file__).resolve().parents[1];D=R/'Data'; checks=[]
def read(n):
 with (D/n).open(newline='',encoding='utf-8-sig') as f:return list(csv.DictReader(f))
def eq(got,want,label):
 assert got==want,(label,got,want)
 checks.append(label)
obj=read('source_object_structure.csv');er=read('EndpointRows.csv');steps=read('evaluation_step_coverage.csv');families=read('family_coverage_structure.csv');pr=read('endpoint_precision_reconstruction.csv')
data_path=a.figures/'figure_data.json'
if not data_path.exists():data_path=R/'Data/figure_data.json'
plot=json.loads(data_path.read_text())
eq(len(obj),471,'471 distinct source objects')
eq(len({r['source_doi'] for r in obj}),471,'Publisher source DOI is the object counting key')
matched=[r for r in er if r['status']=='matched'];unresolved=[r for r in er if r['status']=='unresolved']
# Unresolved endpoint statuses are preserved literally by the workbook; inspect by eligible family.
positive_ids={r['family'] for r in families}
eligible=[r for r in er if r['family'] in positive_ids and r['epmc_type'] in ['review','reviews-summary','referee-report','review-article']]
eq(len(eligible),462,'462 eligible review rows from EndpointRows CSV')
eq(len(matched),458,'458 originally matched rows')
eq(len(eligible)-len(matched),4,'Four unresolved rows remain outside the scored cohort')
source_by_doi={r['source_doi']:r for r in obj}; recovered={r['source_doi'] for r in matched}
eq(len(recovered),362,'458 endpoint rows identify 362 distinct publisher objects')
eq(recovered,{r['source_doi'] for r in obj if int(r['endpoint_unique_match'])==1},'Source and endpoint object identities agree')
role_names=['review-article','evaluation-summary','reply']
rc={k:sum(r['source_role']==k and r['source_doi'] in recovered for r in obj) for k in role_names}
eq(rc,{'review-article':212,'evaluation-summary':91,'reply':59},'Matched roles: 212 reports, 91 summaries, 59 replies')
f1=plot['Figure_1']
for key,value in [('matched_rows',len(matched)),('eligible_rows',len(eligible)),('unresolved_rows',4),('objects',rc),('single_EEB',sum(int(r['eeb_candidate_n'])==1 for r in matched)),('multiple_EEB',sum(int(r['eeb_candidate_n'])>1 for r in matched)),('no_URL',sum(not r['epmc_url'] for r in matched))]:eq(f1[key],value,'Figure 1: '+key)
eq([f1['single_EEB'],f1['multiple_EEB'],f1['no_URL']],[404,54,239],'404/54 EEB counterpart multiplicity; 239 rows without URL')
# Population partition from family-level CSV; residual census is a separate census.
frame=[r for r in read('Families.csv') if r['exact_MED']=='1'];census=read('ResidualCensus.csv')
native={r['family'] for r in frame if r['native_review']=='1'};union={r['family'] for r in frame if r['enriched_review']=='1'};fall={r['family'] for r in frame if r['admissible_EEB_fallback']=='1'}
eq(len(frame),3547,'3,547 exact-MED population families')
eq(len(native-union),0,'Declared enrichment preserves native positives')
eq(len(union&fall),0,'Operational fallback adds only residual families')
eq([len(native),len(union-native),len(fall),len(frame)-len(union|fall)],[2312,588,509,138],'Population partition: 2312 + 588 + 509 + 138')
eq(plot['Figure_2']['partition'],[len(native),len(union-native),len(fall),len(frame)-len(union|fall)],'Figure 2 complete partition matches individual family records')
eq(plot['Figure_2']['N'],len(frame),'Figure 2 family denominator')
eq(plot['Figure_2']['combined'],len(union|fall),'Figure 2 combined discovery 3409')
eq({r['family'] for r in census},{r['family'] for r in frame}-union,'Residual census is exactly the 647-family complement')
for r in plot['Figure_2']['census']:
 rr=[v for v in census if v['endpoint_class']==r['class']]
 eq([r['positive'],r['total']],[sum(int(v['upstream_review']) for v in rr),len(rr)],'Figure 2 census '+r['class'])
eq(sum(int(r['upstream_review']) for r in census),512,'512 upstream positives')
eq(sum(int(r['eligible_declared_EEB_fallback']) for r in census),509,'509 operational gains')
eq(plot['Figure_2']['upstream_positive'],512,'Figure 2 upstream total')
# Step accounting starts with individual objects, not reported coverage states.
groups=defaultdict(list)
for o in obj:groups[(o['family'],int(o['step_rank']))].append(o)
eq(len(groups),117,'117 source-declared evaluation steps from object assignments')
recomputed={}
for key,oo in groups.items():
 recovered_n=sum(o['source_doi'] in recovered for o in oo);eeb_n=sum(int(o['EEB_counterparts'])>0 for o in oo)
 state='endpoint_complete' if recovered_n==len(oo) else 'EEB_only' if recovered_n==0 and eeb_n==len(oo) else 'no_exact_EEB_key' if recovered_n==0 and eeb_n==0 else 'partial'
 recomputed[key]={'source_objects':len(oo),'state':state}
eq(Counter(v['state'] for v in recomputed.values()),Counter(endpoint_complete=91,EEB_only=6,no_exact_EEB_key=20),'91 complete, 6 EEB-only, 20 without exact EEB key; no partial steps')
for s in steps:
 q=recomputed[(s['family'],int(s['step_rank']))]
 eq(q['source_objects'],int(s['source_objects']),f'Step object count {s["family"]}/{s["step_rank"]}')
 eq(sum(o['source_doi'] in recovered for o in groups[(s['family'],int(s['step_rank']))]),int(s['reconstructed_objects']),f'Step endpoint coverage {s["family"]}/{s["step_rank"]}')
for cell in plot['Figure_3']['cells']:
 key=(cell['family'],cell['rank']);q=recomputed.get(key,{'source_objects':None,'state':'no_source_step'})
 eq([cell['source_objects'],cell['state']],[q['source_objects'],q['state']],f'Figure 3 cell {key[0]}/{key[1]}')
eq(len(plot['Figure_3']['cells']),180,'Figure 3 has all 60 x 3 source slots')
eq(sum(c['source_objects'] is None for c in plot['Figure_3']['cells']),63,'63 empty source slots')
for st,N in [('review_with_generic_url',2849),('review_with_DOI_url',21),('review_no_url',30)]:
 ff=[f for f in families if f['stratum']==st];eq(len(ff),20,'Figure 3 sample stratum '+st);eq(set(int(f['stratum_N']) for f in ff),{N},'Figure 3 population stratum '+st)
for f in families:
 states=[recomputed[(f['family'],i)]['state']=='endpoint_complete' for i in range(1,int(f['evaluation_steps'])+1)]
 eq(states[0],True,'First step retained in '+f['family'])
 eq(states,sorted(states,reverse=True),'Recovered prefix in '+f['family'])
eq(sum(all(recomputed[(f['family'],i)]['state']=='endpoint_complete' for i in range(1,int(f['evaluation_steps'])+1)) for f in families),37,'37 families have complete source sequences; 23 incomplete')
for pp in plot['Figure_4']:
 for stage in ['all','first','later']:
  oo=[o for o in obj if o['source_role']==pp['role'] and (stage=='all' or (stage=='first' and int(o['step_rank'])==1) or (stage=='later' and int(o['step_rank'])>1))]
  eq(pp[stage],[sum(o['source_doi'] in recovered for o in oo),len(oo)],'Figure 4 '+pp['role']+' / '+stage)
# Independently reconstruct timestamp candidate universes using actual input DOI bindings.
def stamp(x):
 return datetime.fromisoformat(x.replace('Z','+00:00')).replace(tzinfo=timezone.utc) if not datetime.fromisoformat(x.replace('Z','+00:00')).tzinfo else datetime.fromisoformat(x.replace('Z','+00:00')).astimezone(timezone.utc)
def trunc(t,mode):
 return t if mode=='original' else t.replace(microsecond=0) if mode=='seconds' else t.replace(hour=0,minute=0,second=0,microsecond=0)
index={mode:defaultdict(set) for mode in ['original','seconds','day']}
for o in obj:
 for b in json.loads(o['input_bindings']):
  for mode in index:index[mode][(o['family'],b[0].lower(),trunc(stamp(o['source_time_utc']),mode))].add(o['source_doi'])
retained={mode:[] for mode in index};object_retained={mode:set() for mode in index}
for row in pr:
 is_original_match=bool(row['source_doi'])
 for mode in index:
  cs=index[mode][(row['family'],row['input_doi'].lower(),trunc(stamp(row['original_timestamp']),mode))]
  field='original_candidates' if mode=='original' else 'seconds_candidates' if mode=='seconds' else 'day_candidates'
  eq(sorted(cs),sorted(json.loads(row[field])),f'Precision candidate set {row["epmc_record"]}/{row["epmc_id"]}/{mode}')
  if is_original_match and cs=={row['source_doi']}:
   retained[mode].append((row['epmc_record'],row['epmc_id']));object_retained[mode].add(row['source_doi'])
eq([len(retained[m]) for m in index],[458,273,5],'Fixed-row synthetic ablation independently recomputed: 458,273,5')
for i,mode in enumerate(index):
 eq(plot['Figure_5']['endpoint_rows'][i],[len(retained[mode]),458],'Figure 5 endpoint condition '+mode)
 for pp in plot['Figure_5']['objects']:
  n=sum(source_by_doi[doi]['source_role']==pp['role'] for doi in object_retained[mode]);d=rc[pp['role']]
  eq(pp['values'][i],[n,d],'Figure 5 objects '+pp['role']+'/'+mode)
# All printed geometry and labels come from the audited data; semantic constants use frozen ledger.
receipt_path=a.figures/'build_receipt.json'
if not receipt_path.exists():receipt_path=R/'QA/build_receipt.json'
geom=json.loads(receipt_path.read_text())
# Check each actual connector against independently specified document borders.
spec=json.loads((R/'QA/Figure_1_geometry.json').read_text())
expected=[{k:q[k] for k in ['from','to','type','directed']} for q in spec['connectors']]
eq(geom['connectors'],expected,'Figure 1 actual connector coordinates match document-node specification')
node_map={v['id']:v['rect'] for v in spec['nodes']}
def on_border(pt,rect):
 x,y=pt;a,b,w,h=rect
 return (a<=x<=a+w and b<=y<=b+h) and (x in [a,a+w] or y in [b,b+h])
text_bounds=geom['figures'][0]['text_bounds']
for link in spec['connectors']:
 eq(on_border(link['from'],node_map[link['source']]),True,'Figure 1 connector starts on '+link['source'])
 eq(on_border(link['to'],node_map[link['target']]),True,'Figure 1 connector meets '+link['target']+' border')
 x1,y1=link['from'];x2,y2=link['to'];pad=2.1
 # Connector clearance is deliberately checked against every rendered text extent.
 if x1==x2:box=[x1-pad,min(y1,y2),x1+pad,max(y1,y2)]
 else:box=[min(x1,x2),y1-pad,max(x1,x2),y1+pad]
 hits=[]
 for t in text_bounds:
  tx0,ty0,tx1,ty1=t['bounds_pt']
  if min(tx1,box[2])>max(tx0,box[0]) and min(ty1,box[3])>max(ty0,box[1]):hits.append(t['text'])
 eq(hits,[],'Figure 1 connector clear of text: '+link['source']+' to '+link['target'])
 eq(link['directed'],link['type']=='publisher relation','Figure 1 only publisher relations are directed: '+link['source']+' to '+link['target'])

for n in range(1,6):
 tree=ET.parse(a.figures/f'Figure_{n}.svg');eq(len(tree.findall('.//{http://www.w3.org/2000/svg}text')),0,f'Figure {n} has outlined vector labels, preventing font substitution')
eq(plot['Figure_5']['ambiguous_rows'],[0,185,453],'Figure 5 ambiguous row complements')
# Match original aggregate ledger, separately from the reconstruction above.
base=json.loads((D/'baseline_figure_data.json').read_text())
eq(rc,base['Figure_1']['source_objects'],'Recomputed Figure 1 agrees with frozen baseline ledger')
eq([r['all'] for r in plot['Figure_4']],[[q['overall']['numerator'],q['overall']['denominator']] for q in base['Figure_4']['roles']],'Figure 4 agrees with baseline role totals')
report={'status':'PASS','checks_passed':len(checks),'checks':checks,'scope':'All displayed numeric data independently recomputed from supplied CSV records. Original/seconds/day candidates recalculated from source inputs and timestamps. This is a figure-level audit of frozen projections, not a new extraction of the original XML or service responses.','data_sha256':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(D.glob('*.csv'))}}
a.report.write_text(json.dumps(report,indent=2)+'\n');print(f'PASS: {len(checks)} independent scientific checks.')
