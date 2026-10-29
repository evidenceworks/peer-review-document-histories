"""Offline figure build. Physical coordinates are points, with y increasing downwards."""
from pathlib import Path
import argparse, csv, json, io, re, hashlib, os, tempfile
from collections import Counter
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--output',type=Path,required=True)
a=p.parse_args(); ROOT=Path(__file__).resolve().parents[1]; D=ROOT/'Data'
for name,expected in json.loads((D/'input_manifest.json').read_text()).items():
 if hashlib.sha256((D/name).read_bytes()).hexdigest()!=expected:raise ValueError('Frozen figure input changed: '+name)
OUT=a.output.resolve(); OUT.mkdir(parents=True,exist_ok=False)
os.environ.setdefault('MPLCONFIGDIR',tempfile.mkdtemp(prefix='figure-font-cache-'))
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle, Polygon
from matplotlib import font_manager
from PIL import Image
# Use a locally available serif family. Matplotlib includes DejaVu Serif as a fallback.
FONT_FAMILY = None
FONT_FILES = {}
for family in ('Liberation Serif', 'Times New Roman', 'DejaVu Serif'):
 try:
  faces = {}
  for weight in ('normal', 'bold'):
   prop = font_manager.FontProperties(family=[family], weight=weight)
   path = Path(font_manager.findfont(prop, fallback_to_default=False)).resolve()
   face = font_manager.get_font(str(path))
   if face.family_name != family or face.style_name not in ({'Regular', 'Book'} if weight == 'normal' else {'Bold'}):
    raise ValueError('Missing requested font face')
   faces[weight] = path
  FONT_FAMILY, FONT_FILES = family, faces
  break
 except ValueError:
  continue
if FONT_FAMILY is None:
 raise RuntimeError('A regular and bold serif font is required')
plt.rcParams.update({'font.family':FONT_FAMILY,'font.size':9,'svg.fonttype':'path','svg.hashsalt':'review-history-figures','hatch.linewidth':.45,'text.color':'#171C21'})
FONT_RECORD={w:{'family':FONT_FAMILY,'file':f.name,'sha256':hashlib.sha256(f.read_bytes()).hexdigest()} for w,f in FONT_FILES.items()}
NAVY='#244F68'; BLUE='#6697AF'; GOLD='#B78129'; GRAY='#DDE1E4'; INK='#171C21'; MUTED='#49535C'; RULE='#B9C1C7'
W=510; TEXTS=[]; GEOMETRY=[]; RECEIPT=[]
def rows(n):
 with (D/n).open(newline='',encoding='utf-8-sig') as f:return list(csv.DictReader(f))
fam=rows('family_coverage_structure.csv'); steps=rows('evaluation_step_coverage.csv')
roles=['review-article','evaluation-summary','reply']; labels=['Referee reports','Editorial summaries','Author replies']
ep=rows('EndpointRows.csv'); matches=[r for r in ep if r['status']=='matched']
F1={'matched_rows':len(matches),'eligible_rows':sum(int(r['endpoint_rows']) for r in fam),'unresolved_rows':sum(int(r['unresolved_rows']) for r in fam),'objects':{k:sum(int(r[k+'_retrieved']) for r in fam) for k in roles},'single_EEB':sum(int(r['eeb_candidate_n'])==1 for r in matches),'multiple_EEB':sum(int(r['eeb_candidate_n'])>1 for r in matches),'no_URL':sum(not r['epmc_url'] for r in matches)}
pop=[r for r in rows('Families.csv') if r['exact_MED']=='1']
census=rows('ResidualCensus.csv')
native=sum(int(r['native_review']) for r in pop); union=sum(int(r['enriched_review']) for r in pop); fallback=sum(int(r['admissible_EEB_fallback']) for r in pop)
classes=['publication_only','reachable_PPR_no_elife_event','no_reachable_PPR']
F2={'N':len(pop),'partition':[native,union-native,fallback,len(pop)-union-fallback],'combined':sum(int(r['combined_event_presence']) for r in pop),'census':[{'class':cl,'positive':sum(int(r['upstream_review']) for r in census if r['endpoint_class']==cl),'total':sum(r['endpoint_class']==cl for r in census)} for cl in classes],'upstream_positive':sum(int(r['upstream_review']) for r in census)}
rd=rows('first_later_role_accounting.csv'); F4=[]
for key,label in zip(roles,labels):
 r={q['evaluation_step_position']:[int(q['endpoint_reconstructed']),int(q['source_objects'])] for q in rd if q['stratum']=='all' and q['role']==key}
 r['all']=[sum(r[t][i] for t in ['first','later']) for i in [0,1]]
 F4.append({'role':key,'label':label,**r})
pr=rows('precision_by_role_and_stratum.csv'); conditions=['original','seconds','day']
F5={'endpoint_rows':[[int(q['same_unique_rows']),int(q['originally_matched_rows'])] for c in conditions for q in pr if q['dimension']=='all' and q['precision']==c], 'objects':[]}
for role,label in zip(roles,labels):
 F5['objects'].append({'role':role,'label':label,'values':[[int(q['objects_with_any_retained_row']),int(q['originally_reconstructed_objects'])] for c in conditions for q in pr if q['dimension']=='source_role' and q['group']==role and q['precision']==c]})
DATA={'Figure_1':F1,'Figure_2':F2,'Figure_3':{'cells':[]},'Figure_4':F4,'Figure_5':F5}

def canvas(h):
 fig=plt.figure(figsize=(W/72,h/72),dpi=72)
 ax=fig.add_axes([0,0,1,1]); ax.set(xlim=(0,W),ylim=(h,0));ax.axis('off')
 fig.patch.set_facecolor('white');ax.set_facecolor('white');return fig,ax

def txt(ax,x,y,s,size=9,weight='normal',ha='left',color=INK):
 face=font_manager.get_font(str(FONT_FILES[weight]))
 missing={c for c in str(s) if c not in '\n\r\t' and ord(c) not in face.get_charmap()}
 if missing:raise RuntimeError('Missing glyphs in the selected serif font: '+repr(missing))
 t=ax.text(x,y,str(s),fontproperties=font_manager.FontProperties(fname=str(FONT_FILES[weight]),size=size),ha=ha,va='center',color=color,linespacing=1.25)
 TEXTS.append(t);return t

def line(ax,x1,y1,x2,y2,color=RULE,lw=.6,dash=None):
 return ax.plot([x1,x2],[y1,y2],color=color,lw=lw,linestyle=dash or '-',solid_capstyle='butt')[0]

def rect(ax,x,y,w,h,fill='white',edge=RULE,lw=.6,hatch=None):
 r=Rectangle((x,y),w,h,facecolor=fill,edgecolor=edge,linewidth=lw,hatch=hatch);ax.add_patch(r);return r

def arrow(ax,x1,y1,x2,y2):
 # The exact tip is the destination border. Shaft stops at the triangle base.
 dx=x2-x1;dy=y2-y1;dist=(dx*dx+dy*dy)**.5;ux=dx/dist;uy=dy/dist
 length=5;half=2.1;bx=x2-length*ux;by=y2-length*uy
 line(ax,x1,y1,bx,by,color=MUTED,lw=.8)
 ax.add_patch(Polygon([(x2,y2),(bx-half*uy,by+half*ux),(bx+half*uy,by-half*ux)],facecolor=MUTED,edgecolor='none'))
 GEOMETRY.append({'from':[x1,y1],'to':[x2,y2],'type':'publisher relation','directed':True})

def pct(n,d):return '100%' if n==d else f'{100*n/d:.2f}%'
def save(fig,n):
 global TEXTS
 fig.canvas.draw(); renderer=fig.canvas.get_renderer();bounds=[]
 for t in TEXTS:
  b=t.get_window_extent(renderer);bounds.append({'text':t.get_text(),'bounds_pt':[round(b.x0,3),round(fig.bbox.height-b.y1,3),round(b.x1,3),round(fig.bbox.height-b.y0,3)],'font_pt':t.get_fontsize(),'font_file':Path(t.get_fontproperties().get_file()).name})
 # Only text/text collisions are checked here. Containment in boxes is intentional.
 collisions=[]
 for i,b in enumerate(bounds):
  x0,y0,x1,y1=b['bounds_pt']; assert x0>=0 and y0>=0 and x1<=W and y1<=fig.bbox.height, b
  for c in bounds[:i]:
   u0,v0,u1,v1=c['bounds_pt']
   if min(x1,u1)-max(x0,u0)>.3 and min(y1,v1)-max(y0,v0)>.3:collisions.append([b['text'],c['text']])
 if collisions:raise ValueError(f'Figure {n} text collisions: {collisions}')
 svg=OUT/f'Figure_{n}.svg'; fig.savefig(svg,format='svg',metadata={'Date':None,'Creator':None})
 st=svg.read_text();st=re.sub(r'<metadata>.*?</metadata>','',st,flags=re.S);st=re.sub(r'<!--.*?-->','',st,flags=re.S);svg.write_text(st)
 for ext,dpi in [('png',300),('tif',600)]:
  buf=io.BytesIO();fig.savefig(buf,format='png',dpi=dpi,metadata={'Software':None});buf.seek(0)
  im=Image.open(buf).convert('RGB');kw={'compression':'tiff_lzw'} if ext=='tif' else {}
  target=OUT/f'Figure_{n}.{ext}'
  # Finish encoding before one atomic write; no live encoder shares a final path.
  encoded=io.BytesIO();im.save(encoded,format='TIFF' if ext=='tif' else 'PNG',dpi=(dpi,dpi),**kw)
  payload=encoded.getvalue()
  if ext=='tif':
   with Image.open(io.BytesIO(payload)) as check:
    check.load();end=max(int(a)+int(b) for a,b in zip(check.tag_v2[273],check.tag_v2[279]))
   order='little' if payload[:2]==b'II' else 'big';directory=int.from_bytes(payload[4:8],order)
   if directory-end==1:payload=payload[:end]+b'\x00'+payload[directory:]
  temporary=target.with_suffix(target.suffix+'.tmp')
  with temporary.open('wb') as stream:
   stream.write(payload);stream.flush();os.fsync(stream.fileno())
  os.replace(temporary,target)
  with Image.open(target) as decoded:
   decoded.load();assert decoded.size==im.size and decoded.mode=='RGB'
 RECEIPT.append({'figure':n,'width_pt':W,'height_pt':fig.bbox.height,'width_mm':W/72*25.4,'text_bounds':bounds,'text_collisions':[],'min_font_pt':min(t.get_fontsize() for t in TEXTS)})
 plt.close(fig);TEXTS=[]

# 1. Three documentary representations; only declared publisher relations are directed.
fig,ax=canvas(326)
txt(ax,16,20,'Documentary representations of review events',11.5,'bold')
for x,w,title in [(16,146,'A. Publisher DocMap'),(196,142,'B. EEB DocMap'),(374,120,'C. Europe PMC')]:
 txt(ax,x,47,title,10,'bold',color=NAVY)
 line(ax,x,58,x+w,58,color=RULE,lw=.65)
# Publisher-declared path; correspondence nodes share one horizontal centreline.
rect(ax,23,74,132,42,fill='white',edge=NAVY,lw=.8)
txt(ax,89,87,'Evaluated preprint',9.5,'bold',ha='center')
txt(ax,89,104,'Input DOI + version',9,ha='center')
rect(ax,23,150,132,54,fill='#F1F4F6',edge=NAVY,lw=.8)
txt(ax,89,166,'Review documents',9.5,'bold',ha='center')
txt(ax,89,185,'Publisher output DOIs',9,ha='center')
rect(ax,23,238,132,42,fill='white',edge=NAVY,lw=.8)
txt(ax,89,251,'Later reviewed preprint',9.5,'bold',ha='center')
txt(ax,89,268,'Publication container',9,ha='center')
arrow(ax,89,116,89,150)
txt(ax,102,133,'Evaluation',8.5,color=MUTED)
arrow(ax,89,204,89,238)
txt(ax,102,221,'Publication',8.5,color=MUTED)
rect(ax,202,150,128,54,fill='#F1F4F6',edge=NAVY,lw=.8)
txt(ax,266,166,'Review output',9.5,'bold',ha='center')
txt(ax,266,185,'Input DOI; type: review',9,ha='center')
txt(ax,266,222,'Publisher output DOI absent',9,ha='center',color=MUTED)
txt(ax,266,237,'Input version absent',9,ha='center',color=MUTED)
rect(ax,376,150,118,54,fill='#F1F4F6',edge=NAVY,lw=.8)
txt(ax,435,166,'Endpoint row',9.5,'bold',ha='center')
txt(ax,435,185,'Input DOI; type: review',9,ha='center')
txt(ax,435,222,'URL may be absent',9,ha='center',color=MUTED)
# Observed matching relationships are undirected: no implied service-to-service transfer.
for x1,x2 in [(155,202),(330,376)]:
 line(ax,x1,177,x2,177,color=MUTED,lw=.8,dash=(0,(3,2)))
 GEOMETRY.append({'from':[x1,177],'to':[x2,177],'type':'correspondence','directed':False})
# This legend has only a process arrow and a plain dashed correspondence line.
line(ax,16,308,36,308,color=MUTED,lw=.8)
ax.add_patch(Polygon([(41,308),(36,305.9),(36,310.1)],facecolor=MUTED,edgecolor='none'))
txt(ax,48,308,'Publisher-declared relation',9)
line(ax,267,308,292,308,color=MUTED,lw=.8,dash=(0,(3,2)))
txt(ax,299,308,'Documentary correspondence',9)
DATA['Figure_1']['connectors']=GEOMETRY.copy();save(fig,1)

# 2. One exhaustive partition, followed by the three census denominators.
fig,ax=canvas(363)
txt(ax,16,20,'A. Review-event discovery',12,'bold')
txt(ax,16,39,f'Review event identified for {F2["combined"]:,} of {F2["N"]:,} families ({pct(F2["combined"],F2["N"])}).',10.5)
x=16; bw=478
for val,col in zip(F2['partition'],[NAVY,BLUE,GOLD,GRAY]):
 width=bw*val/F2['N'];rect(ax,x,59,width,30,fill=col,edge='white',lw=.7,hatch='///' if col==GOLD else None)
 txt(ax,x+width/2,74,f'{val:,}',10,'bold',ha='center',color='white' if col==NAVY else INK);x+=width
for x,y,c,label,val in [(16,113,NAVY,'Native coverage',F2['partition'][0]),(267,113,BLUE,'Declared-link addition',F2['partition'][1]),(16,139,GOLD,'Operational EEB addition',F2['partition'][2]),(267,139,GRAY,'Remaining uncovered',F2['partition'][3])]:
 rect(ax,x,y-4.5,11,9,fill=c,edge='white',lw=.45,hatch='///' if c==GOLD else None);txt(ax,x+18,y,label,10.5)

txt(ax,16,174,'B. Review-event presence in 647 residual families',12,'bold')
txt(ax,16,198,'Residual class',9.5,color=MUTED);txt(ax,494,198,'Positive / total',9.5,color=MUTED,ha='right')
left=190;length=222
for y,title,r in zip([224,259,294],['Publication event only','Preprint reachable;\nno eLife event','No preprint reachable'],F2['census']):
 txt(ax,16,y,title,10);rect(ax,left,y-6,length*r['total']/400,12,fill=GRAY,edge='none');rect(ax,left,y-6,length*r['positive']/400,12,fill=NAVY,edge='none')
 txt(ax,494,y,f'{r["positive"]}/{r["total"]}',10.5,ha='right')
line(ax,left,313,left+length,313,color=MUTED)
for val in [0,100,200,300,400]:
 xx=left+length*val/400;line(ax,xx,313,xx,317,color=MUTED);txt(ax,xx,328,val,9.5,ha='center')
txt(ax,left+length/2,344,'Families',10,ha='center')
rect(ax,16,325,9,8,fill=NAVY,edge='none');txt(ax,32,329,'Upstream review event',10)
rect(ax,16,341,9,8,fill=GRAY,edge='none');txt(ax,32,345,'No upstream review event',10)

save(fig,2)

# 3. Identical source-step columns in each stratum. Family order is documented.
fig,ax=canvas(471)
txt(ax,16,20,'Evaluation-step coverage in 60 review-positive families',11,'bold')
txt(ax,16,39,'117 source steps; cell values are source-object counts.',9)
strata=[('review_with_generic_url','Generic URL',2849),('review_with_DOI_url','DOI URL',21),('review_no_url','No URL',30)]
for base,(st,title,popn) in zip([16,184,352],strata):
 txt(ax,base,65,title,10,'bold')
 txt(ax,base+42,88,'Family',8,ha='right',color=MUTED)
 for j,lab in enumerate(['First','Second','Third']):txt(ax,base+63+j*32,88,lab,8,ha='center',color=MUTED)
 group=sorted([r for r in fam if r['stratum']==st],key=lambda r:(int(r['absent_steps'])>0,int(r['evaluation_steps']),int(r['family'])))
 for i,f in enumerate(group):
  yy=107+i*15.5;txt(ax,base+42,yy,f['family'],9,ha='right')
  for j in range(1,4):
   q=next((q for q in steps if q['family']==f['family'] and int(q['step_rank'])==j),None)
   col='white' if q is None else NAVY if q['coverage_state']=='complete' else GOLD if q['EEB_coverage_state']=='complete' else GRAY
   hatch='///' if q is not None and q['coverage_state']=='none' and q['EEB_coverage_state']=='complete' else None
   rect(ax,base+49+(j-1)*32,yy-6.25,28,12.5,fill=col,edge='#939DA5',lw=.45,hatch=hatch)
   if q:txt(ax,base+63+(j-1)*32,yy,q['source_objects'],9,'bold',ha='center',color='white' if col==NAVY else INK)
   DATA['Figure_3']['cells'].append({'family':f['family'],'stratum':st,'rank':j,'source_objects':None if q is None else int(q['source_objects']),'state':'no_source_step' if q is None else 'endpoint_complete' if col==NAVY else 'EEB_only' if col==GOLD else 'no_exact_EEB_key'})
for x,y,col,h,label in [(16,432,NAVY,None,'Endpoint complete (91 steps)'),(267,432,GOLD,'///','EEB only (6 steps)'),(16,453,GRAY,None,'No exact EEB key (20 steps)'),(267,453,'white',None,'No source step')]:
 rect(ax,x,y-5,14,10,fill=col,edge='#939DA5',lw=.45,hatch=h);txt(ax,x+21,y,label,8.4)

save(fig,3)

# 4. Stage columns prevent a later-step denominator being read as an overall total.
fig,ax=canvas(316)
txt(ax,16,20,'Source-document reconstruction by role and stage',11,'bold')
txt(ax,16,39,'Distinct publisher objects in 60 sampled review-positive families.',9)
for x,stage,label in zip([124,251,378],['all','first','later'],['All source objects','First step','Later steps']):
 txt(ax,x+53,64,label,9,'bold',ha='center')
 for y,r in zip([112,184,256],F4):
  n,d=r[stage];rect(ax,x,y-6,106,12,fill=GRAY,edge='none');rect(ax,x,y-6,106*n/d,12,fill=NAVY,edge='none')
  txt(ax,x,y+19,f'{n}/{d}',9);txt(ax,x+106,y+19,pct(n,d),9,ha='right')
for y,label in zip([112,184,256],['Referee\nreports','Editorial\nsummaries','Author\nreplies']):txt(ax,16,y,label,9.5,'bold')
for y in [151,223]:line(ax,16,y,494,y,color='#D8DDE1',lw=.5)
txt(ax,124,84,'Reconstructed share of source objects (0-100%)',8.6,color=MUTED)
rect(ax,16,298,10,8,fill=NAVY,edge='none');txt(ax,33,302,'Reconstructed',9)
rect(ax,267,298,10,8,fill=GRAY,edge='none');txt(ax,284,302,'Not reconstructed',9)
save(fig,4)

# 5. Retention and ambiguity in rows; a separate percentage matrix for source objects.
fig,ax=canvas(504)
txt(ax,16,20,'Sensitivity to timestamp precision',12.5,'bold')
txt(ax,16,39,'Synthetic truncation of UTC timestamps',11)
txt(ax,16,65,'A. Retention among endpoint rows',12,'bold')
txt(ax,16,84,'458 originally matched rows',10.5)
bar_x=170;bar_w=180
for pc in [0,50,100]:txt(ax,bar_x+bar_w*pc/100,105,f'{pc}%',9.5,ha='center',color=MUTED)
txt(ax,432,105,'Retained',9.9,'bold',ha='right');txt(ax,494,105,'Ambiguous',9.6,'bold',ha='right')
for y,label,(n,d) in zip([132,166,200],['Original precision','Truncated to seconds','Truncated to UTC day'],F5['endpoint_rows']):
 txt(ax,16,y,label,10.5)
 rect(ax,bar_x,y-10,bar_w,20,fill=GRAY,edge='none')
 rect(ax,bar_x,y-10,bar_w*n/d,20,fill=NAVY,edge='none')
 txt(ax,432,y-6,f'{n}/{d}',11,'bold',ha='right');txt(ax,432,y+7,pct(n,d),10,ha='right',color=MUTED)
 txt(ax,494,y,d-n,11,ha='right')
rect(ax,16,225,11,9,fill=NAVY,edge='none');txt(ax,34,229,'Same source match retained',10)
rect(ax,307,225,11,9,fill=GRAY,edge='none');txt(ax,325,229,'Ambiguous match',10)
txt(ax,16,260,'B. Retention among distinct source objects',12,'bold')
txt(ax,16,281,'Objects with at least one retained unique row match',10.2)
cols=[172,280,388];cell_w=102;cell_h=47
for x,label in zip(cols,['Original\nprecision','Truncated to\nseconds','Truncated to\nUTC day']):txt(ax,x+cell_w/2,310,label,10.5,ha='center')
from matplotlib.colors import to_rgb,to_hex
lo=to_rgb('#F1F3F5');hi=to_rgb(NAVY)
def retention_color(f):return to_hex(tuple(a+(b-a)*f for a,b in zip(lo,hi)))
for y,r in zip([326,379,432],F5['objects']):
 txt(ax,16,y+cell_h/2,r['label'],10.7,'bold')
 for x,(n,d) in zip(cols,r['values']):
  f=n/d;col=retention_color(f);fg='white' if f>=.80 else INK
  rect(ax,x,y,cell_w,cell_h,fill=col,edge='white',lw=.6)
  txt(ax,x+cell_w/2,y+16,f'{n}/{d}',12,'bold',ha='center',color=fg)
  txt(ax,x+cell_w/2,y+32,pct(n,d),11,ha='center',color=fg)
txt(ax,16,497,'Object retention (%)',10,color=MUTED)
for j in range(100):rect(ax,342+j*1.3,492,2.6,9,fill=retention_color(j/99),edge='none')
txt(ax,334,497,'0',9.5,ha='right',color=MUTED);txt(ax,478,497,'100',9.5,color=MUTED)

F5['ambiguous_rows']=[d-n for n,d in F5['endpoint_rows']]
save(fig,5)
(OUT/'figure_data.json').write_text(json.dumps(DATA,indent=2)+'\n')
(OUT/'build_receipt.json').write_text(json.dumps({'figures':RECEIPT,'connectors':GEOMETRY,'font':FONT_RECORD,'svg_text':'outlined vector paths; real labels remain in source code and text_bounds','source_sha256':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(D.iterdir()) if p.is_file()}},indent=2)+'\n')
print('Built five figures in SVG, PNG (300 dpi), TIFF (600 dpi).')
