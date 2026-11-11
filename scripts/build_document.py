"""Build the revised manuscript from saved offline evidence, without network access."""
import gzip
import json
from pathlib import Path
from docx import Document
from docx.shared import Inches, Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.enum.table import WD_TABLE_ALIGNMENT, WD_CELL_VERTICAL_ALIGNMENT

ROOT=Path(__file__).resolve().parents[1]
RESULT=ROOT/'results/offline-v2'
s=json.loads((RESULT/'summary.json').read_text())
config=json.loads((RESULT/'config.json').read_text())
refs=json.loads((ROOT/'paper/references.json').read_text())
checks=json.loads((ROOT/'results/invariants.json').read_text())
replay=json.loads((ROOT/'results/replay.json').read_text())
comparison=json.loads((ROOT/'results/reproduction.json').read_text())
cells={(c['strategy'],c['book_aware'],c['control']):c for c in s['cells']}
labels={'idle':'Idle','taker':'Market taker','market_maker':'Market maker','repricer':'Repricer','scripted_spoofer':'Scripted spoofer','scripted_layerer':'Scripted layerer'}
sp=cells[('scripted_spoofer',True,False)]; sg=cells[('scripted_spoofer',True,True)]
lp=cells[('scripted_layerer',True,False)]; lg=cells[('scripted_layerer',True,True)]
benign=[c for c in s['cells'] if not c['strategy'].startswith('scripted')]
positive=[c for c in s['cells'] if c['strategy'].startswith('scripted') and not c['control']]
positive_policy=[c for c in s['cells'] if c['strategy'].startswith('scripted') and c['control']]

doc=Document()
doc.settings.odd_and_even_pages_header_footer=False
sec=doc.sections[0]
sec.page_width=Inches(8.5); sec.page_height=Inches(11)
sec.top_margin=Inches(.75); sec.bottom_margin=Inches(.7); sec.left_margin=sec.right_margin=Inches(.85)
sec.header_distance=Inches(.3);sec.footer_distance=Inches(.3)
for name in ['Normal','Title','Subtitle','Heading 1','Heading 2','Caption']:
 st=doc.styles[name];st.font.name='Times New Roman';st.font.color.rgb=RGBColor(0,0,0)
for style in doc.styles:
 for border in list(style.element.iter(qn('w:pBdr'))): border.getparent().remove(border)
 if style.name in ['Normal','Title','Subtitle','Heading 1','Heading 2','Caption']:
  fonts=style.element.get_or_add_rPr().find(qn('w:rFonts'))
  if fonts is not None:
   for attr in ['asciiTheme','hAnsiTheme','eastAsiaTheme','cstheme']:
    fonts.attrib.pop(qn('w:'+attr),None)
doc.styles['Caption'].font.bold=False
doc.styles['Caption'].font.italic=True
doc.styles['Caption'].font.size=Pt(9.5)
normal=doc.styles['Normal'];normal.font.size=Pt(11)
normal.paragraph_format.space_after=Pt(7);normal.paragraph_format.line_spacing=1.08
for name,size in [('Title',22),('Heading 1',15),('Heading 2',12)]:
 st=doc.styles[name];st.font.size=Pt(size);st.paragraph_format.space_before=Pt(9);st.paragraph_format.space_after=Pt(7)
header=sec.header.paragraphs[0];header.text='ORDER BOOK COMPLIANCE PILOT';header.runs[0].font.size=Pt(8)
f=sec.footer.paragraphs[0];f.alignment=WD_ALIGN_PARAGRAPH.CENTER
f.add_run('Research draft  |  ')
fld=OxmlElement('w:fldSimple');fld.set(qn('w:instr'),'PAGE');f._p.append(fld)
for r in f.runs:r.font.size=Pt(9)
doc.core_properties.title='Order Book Compliance Controls Pilot'
doc.core_properties.subject='Offline simulation results and revised research protocol'
doc.core_properties.author=''
doc.core_properties.keywords='offline simulation, surveillance, reproducibility, trading agents'

def p(t,style=None): return doc.add_paragraph(t,style)
def h(t): return doc.add_heading(t,level=1)
def page(): doc.add_page_break()
def table(headers,rows,widths):
 t=doc.add_table(rows=1,cols=len(headers));t.alignment=WD_TABLE_ALIGNMENT.CENTER;t.autofit=False
 for col,w in zip(t.columns,widths):col.width=Inches(w)
 for cell,w in zip(t.rows[0].cells,widths):cell.width=Inches(w)
 for cell,text in zip(t.rows[0].cells,headers):cell.text=text
 trpr=t.rows[0]._tr.get_or_add_trPr();repeat=OxmlElement('w:tblHeader');trpr.append(repeat)
 for row in rows:
  cells_=t.add_row().cells
  for cell,text,w in zip(cells_,row,widths):cell.text=str(text);cell.width=Inches(w)
 for i,row in enumerate(t.rows):
  for j,cell in enumerate(row.cells):
   cell.vertical_alignment=WD_CELL_VERTICAL_ALIGNMENT.CENTER
   tcpr=cell._tc.get_or_add_tcPr()
   borders=OxmlElement('w:tcBorders')
   for side in ['top','left','bottom','right']:
    el=OxmlElement('w:'+side);el.set(qn('w:val'),'single');el.set(qn('w:sz'),'4');el.set(qn('w:color'),'D9D9D9');borders.append(el)
   tcpr.append(borders)
   margins=OxmlElement('w:tcMar')
   for side in ['top','left','bottom','right']:
    el=OxmlElement('w:'+side);el.set(qn('w:w'),'85');el.set(qn('w:type'),'dxa');margins.append(el)
   tcpr.append(margins)
   if i==0:
    sh=OxmlElement('w:shd');sh.set(qn('w:fill'),'E8EDF2');tcpr.append(sh)
   for par in cell.paragraphs:
    par.paragraph_format.space_after=Pt(2);par.paragraph_format.space_before=Pt(2);par.paragraph_format.line_spacing=1
    if j>0 and len(headers)>2:par.alignment=WD_ALIGN_PARAGRAPH.CENTER
    for r in par.runs:r.font.size=Pt(9.5);r.bold=i==0
  cant=OxmlElement('w:cantSplit');row._tr.get_or_add_trPr().append(cant)
 p('')
 return t

p('Order Book Compliance Controls Pilot','Title')
p('Offline evidence and a revised protocol for studying trading agents','Subtitle')
p('Research draft revised 6 November 2026  |  Protocol version 0.2')
h('Abstract')
p(f'We investigate the measurement infrastructure needed to study suspicious order flow from autonomous trading agents. This pilot implements a price and time priority limit order book, declared benign strategies, scripted manipulation controls, pattern detectors, and an independent order gate. We run {s["calibration_episode_count"]} calibration episodes and {s["episode_count"]} evaluation episodes with disjoint seeds. Both scripted positive controls trigger conduct-pattern alerts in every unguarded evaluation episode; the policy arm removes those alerts in the tested grid. The effect is evaluated alongside filled volume, inactivity, inventory and terminal wealth. For the scripted spoofer in the book-aware market, mean filled volume changes from {sp["filled_lots"]:.1f} to {sg["filled_lots"]:.1f} lots, while mean absolute midpoint error changes from {sp["mean_abs_price_error"]:.4f} to {sg["mean_abs_price_error"]:.4f} ticks. These are generated-strategy results, not observations of LLM emergence or legal findings. No LLM inference was completed. Recorded actions, full-event hashes, tests and deterministic replay make the pilot reproducible. The evidence supports further testing of the infrastructure; it does not establish real-market efficacy.')
h('1 Introduction')
p('The original research question remains whether an LLM given a profit objective and ordinary order-entry tools produces suspicious order sequences without a malicious instruction. Answering that question requires reliable separation of proposed actions, actual executions, surveillance alerts, stated explanations and economic consequences. A lower alert count can result from useful prevention, loss of trading activity, or an incomplete detector.')
p('This revision replaces the original draft\'s placeholder results with an implemented offline pilot. It narrows the contribution to a reproducible simulator and a development study of measurement and enforcement. Claims about four model families, prompt effects, stated intent rates and multi-agent coordination are withdrawn pending actual experiments. The original PDF remains unchanged; the revision record maps each review issue to its resolution.')

page();h('2 Background and contribution boundary')
p('Byrd demonstrated that a reinforcement-learning trader can learn spoofing and examined normative guidance [1]. Wang and Wellman studied spoofing in agent-based limit order books [4]. Agrawal and colleagues already examined LLM collusion in continuous double auctions [2]; continuity alone is therefore not a novel contribution. ReguSim separates reasoning, attempted actions, enforcement and monitoring evidence, but its trading environment has exogenous prices without order-book interaction or market impact [3]. This artifact focuses on explicit order lifecycles and replay. No claim of being the first such system is made.')
p('Financial market-abuse judgment benchmarks ask a different question from executed order-flow experiments [9]. An agent\'s explanation can also be unfaithful to its computation [5]. The pilot therefore reports observable patterns and known scripted policies; it does not infer psychological or legal intent.')
h('3 Environment and accounting')
p('The matching engine maintains integer prices and quantities, price priority followed by submission order, partial fills, and cancellation ownership. Unfilled market quantity expires. A subject starts with zero cash and inventory. Every fill charges each participant 0.05 ticks per lot. Both experimental arms impose a 100-lot exposure bound that includes outstanding same-side orders. The reported wealth measure is cash plus inventory valued at the simulator\'s exogenous fundamental; it is not liquidation proceeds.')
table(['Parameter','Pilot setting'],[
 ['Episode and decisions','720 seconds; 12 decisions every 60 seconds'],
 ['Background evolution','Every second, including between subject actions'],
 ['Liquidity supply','10 lots at each of three levels on each side'],
 ['Reference price','Starts at 1000; seeded changes every 30 seconds'],
 ['Book-aware condition','Two-tick quote shift when absolute imbalance exceeds 0.4'],
 ['Noise orders','Seeded side and size; arrivals every 10 seconds'],
 ['Calibration and evaluation','Seeds 0 to 19 and fresh seeds 2000 to 2039']
],[2.0,4.6])
p('The book-aware liquidity response is deliberately engineered and is not fitted to exchange data. Quotes refresh after every action, which permits rapid reactions to displayed size. Random fundamentals and noise orders are generated in advance, so agent actions do not consume or shift random draws. Treatments share exogenous paths within seed, although their endogenous books diverge. Background agents have no production margin, latency or inventory model.')

page();h('4 Detectors and enforcement')
p('Each alert records its type, order identifiers and simulated time. The primary endpoint is the episode union of spoof-pattern, layer-pattern and self-match alerts. Excessive cancellation remains an operational diagnostic outside that union. A pattern alert is not proof of abuse, manipulative intent, or profitable manipulation.')
table(['Signal','Operational definition'],[
 ['Spoof pattern','A nonmarketable order of at least three times external touch depth, within three ticks of touch, followed by an opposite-side fill and cancellation within 120 seconds; less than 10 percent executed'],
 ['Layer pattern','At least three distinct same-side price levels with overlapping lifetimes around a shared opposite-side fill, then cancellations; each satisfies the timing and low-fill conditions'],
 ['Self-match','A fill with the subject on both sides; tested with fixtures, not a wash-strategy experiment'],
 ['Excessive cancels','Rolling cancel-to-submit ratio above the calibration threshold, with at least 20 submissions']
],[1.35,5.25])
p('Only the cancellation threshold is fitted: the empirical 99th percentile of episode maximum ratios in 160 calibration episodes. Other thresholds are declared design constants. The fitted threshold is 1.0. Because these 12-minute episodes fit inside the 30-minute cancellation window, the diagnostic cannot exceed that value in this cohort. Its zero alerts are uninformative and are not counted as evidence of successful cancellation detection. A longer-session redesign needs separate calibration before a future study.')
p('The optional policy gate limits each limit submission to twice external touch depth, limits same-side resting levels to two, and prevents predicted self-matches by walking the book in matching order and accounting for executable quantity. When cancellation ratio exceeds 0.9 after 20 submissions, the gate throttles new submissions. Cancellations remain possible. A development defect that rejected orders even when better-priced external liquidity would fill them first was corrected and covered by a regression test before the final batch.')
p('The gate and surveillance rules overlap. Fewer alerts therefore demonstrate compliance with these implemented predicates, not independent validation of market-conduct compliance. Volume, wealth and midpoint error supply additional diagnostics. They do not establish welfare gains, legal compliance, robustness to evasion, or safety under concurrent real-world execution.')

page();h('5 Experimental design and results')
p('The final evaluation crosses six scripted policies, two market conditions and two enforcement settings with 40 fresh seeds per cell, giving 960 episodes. Four policies are declared benign controls: idle, alternating market taker, small symmetric market maker and distant repricer. The two positive controls are explicitly programmed spoof and layer sequences. All fixed cells are retained. No condition is selected by observed alert rate.')
rows=[]
for name in config['strategies']:
 for guard in [False,True]:
  c=cells[(name,True,guard)]
  rows.append([labels[name], 'On' if guard else 'Off',f'{c["conduct_pattern"]["k"]}/40',f'{c["filled_lots"]:.2f}',f'{c["wealth_fundamental"]:.2f}',f'{c["mean_abs_price_error"]:.4f}'])
table(['Strategy','Gate','Alerted','Filled lots','Wealth','Price error'],rows,[1.7,.5,.65,.85,.85,1.05])
p('Table 1. Book-aware condition. Each row contains 40 episodes. Filled lots, fundamental-valued wealth and absolute midpoint error are episode means. Wealth is measured in ticks times lots after fees; price error is measured in ticks. The complete grid and per-detector breakdown are in summary.json and episodes.csv. These rows are scripted controls, not model comparisons.','Caption')
lo=sp['conduct_pattern']['ci95'][0]*100;hi=benign[0]['conduct_pattern']['ci95'][1]*100
p(f'The four benign policies produce no conduct-pattern alerts in any evaluated cell. For a single cell, 0 of 40 has a Wilson 95 percent interval of 0 to {hi:.1f} percent; this is not evidence of zero population risk. A 40-of-40 cell has an interval of {lo:.1f} to 100 percent. Cells sharing seeds or strategy definitions are not pooled into a fictitious independent sample. Positive-control coverage does not estimate recall for unknown manipulation.')
p('Policy effects are paired within seed. The released summary gives 2000-resample percentile bootstrap intervals for alert incidence, volume, wealth and midpoint error. These exploratory intervals quantify variation over the specified synthetic paths, not structural uncertainty about real markets. No significance claims, model rankings or confirmatory hypotheses are reported.')

page();h('6 What the controls change')
p(f'In the book-aware condition, the scripted spoofer retains {sg["filled_lots"]:.1f} mean filled lots with the gate, compared with {sp["filled_lots"]:.1f} without it. Fundamental-valued wealth changes from {sp["wealth_fundamental"]:.2f} to {sg["wealth_fundamental"]:.2f}. The gate rejects the large displayed orders while the small opposite-side market orders can still execute. This provides an example where fewer alerts do not merely reflect complete inactivity. It does not show that a learning agent would respond in the same way.')
p(f'The layerer fills {lp["filled_lots"]:.1f} lots without the gate and {lg["filled_lots"]:.1f} with it, with {lg["rejected_actions"]:.1f} mean rejected actions in the guarded cell. This tradeoff must be reported alongside the alert reduction. The benign repricer also loses some filled volume under the submission throttle, demonstrating a cost on the negative controls. For all strategies, the artifact also reports no-fill inactivity and the difference between midpoint-valued and fundamental-valued wealth. Ratios to baseline profit are avoided because baseline wealth can be zero or negative.')
noaware=cells[('scripted_spoofer',False,False)]
p(f'Without the engineered book response, the unguarded spoofer still triggers its pattern detector, but mean fundamental-valued wealth is {noaware["wealth_fundamental"]:.2f}, compared with {sp["wealth_fundamental"]:.2f} in the responsive market. This distinguishes suspicious order form from profitable exploitation in this simulator. The responsive population was designed to react to imbalance, so the comparison is a mechanism check under stipulated assumptions, not discovery of a real-market vulnerability.')
h('A recorded positive control')
with gzip.open(RESULT/'traces.jsonl.gz','rt') as f:
 record=next(json.loads(line) for line in f if (lambda r:r['strategy']=='scripted_spoofer' and r['book_aware'] and not r['control'] and r['seed']==2000)(json.loads(line)))
trace=[]
for e in record['events']:
 if e['time']>5:break
 if e['kind']=='submit':trace.append([e['time'],'Submit',e['side'],e['qty'],'Market' if e['price'] is None else e['price']])
 elif e['kind']=='fill':trace.append([e['time'],'Fill','Sell' if e['seller']=='subject' else 'Buy',e['qty'],e['price']])
 elif e['kind']=='cancel':trace.append([e['time'],'Cancel',e['side'],e['qty'],e['price']])
table(['Second','Event','Side','Lots','Price'],trace,[.7,1.35,1.05,1.0,1.5])
p('Table 2. The first decision of seed 2000, scripted spoofer, book-aware market, no policy. The example is selected by a fixed identifier, not by exceptional profit. It is a programmed positive control; there is no model reasoning quotation.','Caption')
p('Jointly multiplying spoof size and timing thresholds by 0.75 and 1.25 leaves the episode conduct-pattern classifications unchanged in this grid. The layer-count and cancellation settings are not varied. This narrow sensitivity check does not substitute for an independently designed detector, expert adjudication or varied manipulation strategies.')

page();h('7 Verification and reproducibility')
p(f'The final implementation passes 26 unit tests covering price and time priority, partial fills, market-order expiry, cancellation ownership, fee accounting, position conservation, outstanding-order exposure, control behavior, detector positives and near misses, deterministic seeds and interval calculations. The finite-state checker evaluates {checks["transitions_checked"]} submission transitions across {checks["states"]} feasible states for a three-lot bound, without failures. It checks the exposure acceptance predicate on that bounded domain. It is not a theorem about arbitrary quantities, the complete simulator, or legal compliance.')
p(f'The action replay reconstructs all {replay["episodes_replayed"]} final evaluation episodes from their seeds and saved attempted actions, without invoking the strategy functions. It reproduces every complete event-stream hash, every alert and every reported metric. A separately generated batch is byte-identical for config.json, calibration.json, episodes.csv, summary.json and traces.jsonl.gz. The result of that comparison is {str(comparison["equal"]).lower()}. Saved manifests identify the Python version, source hashes and evidence hashes.')
p('The compressed trace archive retains subject-related events and sampled states, not every background event. Complete background histories are regenerated from the documented streams. Hash agreement verifies reproducibility relative to this code and evidence; it is not an independent witness that the market abstraction is correct. The development run and its original code are archived separately, including the overly conservative gate and its reproduction checks.')
h('Reproduction instructions')
p('From research/agentic_compliance, run the following with Python 3.9 or later. The measured run uses the interpreter identified in the manifest. The simulator requires only the standard library; no model account or network is needed.')
for command in ['python3 -m unittest discover -s tests -v','python3 -m scripts.check_invariants','python3 -m pilot.run --output results/local-new-run','python3 -m scripts.replay results/offline-v2','python3 -m scripts.compare_results results/offline-v2 results/local-new-run']:
 par=p(command)
 for r in par.runs:r.font.name='Courier New';r.font.size=Pt(9)
p('Use a new output directory. The runner refuses to overwrite existing evidence. The editable manuscript is generated by build_document.py with python-docx 1.2.0; the PDF is exported from that document. Office rendering is distinct from deterministic numerical reproduction.')
h('Ethics and evidence handling')
p('All orders exist only inside the simulator. No brokerage integration, real-market experiment, human-subject annotation or institutional ethics approval is claimed. The provider authentication check returned HTTP 401 before inference; the user chose offline evaluation. No credentials or private provider responses are included. This private repository upload does not constitute a public release or a new software license.')

page();h('8 Implications and next study')
p('This pilot supports a narrow engineering conclusion: order-level evidence can be replayed and control effects can be examined without conflating rejected requests with executed trades. It does not answer whether LLM agents discover manipulation. The revised title and abstract reflect that boundary. Results on the engineered liquidity response should not be extrapolated to high-frequency markets, calibrated equities, other venues or multi-agent competition.')
p('The environment omits real latency, queue uncertainty, inventory-managed background agents, market fragmentation, a closing auction and terminal liquidation. The subject can act only at scheduled intervals, while the programmed controls receive a favorable short sequence within a decision. A policy can suppress useful activity or be evaded by strategies not represented here. The cancellation diagnostic is uninformative at this horizon. The statistical intervals cannot repair these structural limitations.')
p('A future LLM study should freeze exact model identifiers, prompts, observations, action schemas, call limits and failure handling. P0 should specify a profit objective; P2 should add explicit conduct prohibitions. Run matched fresh seeds for both prompts and enforcement settings, retain all failed and inactive episodes, and size the study for a prespecified effect rather than inheriting 20 episodes per cell. Thresholds and condition selection must use separate pilot data. No such prompt comparison is claimed here.')
p('Explanation labeling must be behavior-specific. For spoofing and layering, identify explicit statements about false displayed interest and intended cancellation. For wash trading, identify statements about artificial turnover through executions. For marking the close, identify statements about influencing the closing reference for position benefit. Retain explicit benign explanation, manipulative explanation, conflicting evidence and unavailable explanation as distinct categories. Two annotators require a third adjudicator on disagreement. Missing explanation does not establish innocence, and explanations do not establish internal intent [5].')
p('FINRA states that existing technology-neutral obligations continue to apply to generative AI [6]. The SEC Market Access Rule concerns broker-dealer controls [7], while the CFTC spoofing guidance concerns the relevant Commodity Exchange Act provision [8]. This simulator\'s predicates are not interchangeable with those legal tests. Neither zero alerts nor passing a gate certifies compliance.')
p('Commercially, pre-deployment evaluation, model-update regression testing and evidence reconstruction are plausible applications. An investigation assistant could also organize alerts and action records for a human reviewer. These are product hypotheses, not demand estimates or proven analyst-productivity gains. Established surveillance offerings already cover detection and case workflows [10]; differentiation would require demonstrated value from agent-specific evidence, integration and retained trading utility.')
h('9 Conclusion')
p('The offline pilot replaces unsupported empirical claims with a tested and replayable artifact. Scripted positive controls demonstrate the intended mechanism in a stylized responsive book, and policy effects are reported with economic and activity diagnostics. Establishing emergent LLM behavior, faithful intent evidence and practical compliance efficacy remains the next research task.')

page();h('References')
for ref in refs:
 par=p(f'[{ref["id"]}] {ref["text"]}')
 par.paragraph_format.space_after=Pt(2)
 link=p(ref['url'])
 link.paragraph_format.space_after=Pt(10)
 for r in link.runs:r.font.size=Pt(9)
p('References were checked against primary publication or regulator pages during the review. The bibliography deliberately excludes unresolved placeholders from the supplied draft. The ethical-judgment study is attributed to ICAIF 2025 rather than NeurIPS 2026.')
out=ROOT/'paper/agentic_compliance_pilot.docx';doc.save(out)
print(out)
