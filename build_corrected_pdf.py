"""Build the corrected pilot from frozen results. Optional: reportlab==4.4.9."""
import argparse
import json
from pathlib import Path
from xml.sax.saxutils import escape
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_LEFT
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, Preformatted
from reportlab.graphics.shapes import Drawing, Rect, Line, String

ROOT=Path(__file__).resolve().parent
NAVY=colors.HexColor('#173249');TEAL=colors.HexColor('#177B83');GRAY=colors.HexColor('#56616B')
styles={
 'body':ParagraphStyle('body',fontName='Times-Roman',fontSize=11,leading=14.6,spaceAfter=8),
 'small':ParagraphStyle('small',fontName='Times-Roman',fontSize=9.3,leading=12,spaceAfter=6),
 'caption':ParagraphStyle('caption',fontName='Helvetica',fontSize=9,leading=12,spaceAfter=9,textColor=GRAY),
 'h1':ParagraphStyle('h1',fontName='Helvetica-Bold',fontSize=16,leading=20,spaceAfter=14,textColor=NAVY),
 'h2':ParagraphStyle('h2',fontName='Helvetica-Bold',fontSize=11,leading=14,spaceBefore=9,spaceAfter=6,textColor=NAVY),
 'title':ParagraphStyle('title',fontName='Times-Bold',fontSize=23,leading=27,spaceAfter=15,textColor=NAVY),
 'meta':ParagraphStyle('meta',fontName='Helvetica',fontSize=9,leading=13,spaceAfter=14,textColor=GRAY),
 'cell':ParagraphStyle('cell',fontName='Helvetica',fontSize=8.3,leading=11),
 'code':ParagraphStyle('code',fontName='Courier',fontSize=7.7,leading=11,spaceAfter=9),
}


def p(text,style='body'):return Paragraph(text,styles[style])
def table(rows,widths):
    cells=[[p(str(v),'cell') for v in row] for row in rows]
    t=Table(cells,colWidths=widths,repeatRows=1,hAlign='LEFT')
    t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#E9F0F4')),
        ('LINEABOVE',(0,0),(-1,0),.8,NAVY),('LINEBELOW',(0,0),(-1,0),.5,NAVY),
        ('LINEBELOW',(0,-1),(-1,-1),.7,NAVY),('VALIGN',(0,0),(-1,-1),'TOP'),
        ('LEFTPADDING',(0,0),(-1,-1),6),('RIGHTPADDING',(0,0),(-1,-1),6),
        ('TOPPADDING',(0,0),(-1,-1),6),('BOTTOMPADDING',(0,0),(-1,-1),6)]))
    return t


def mechanism_plot():
    d=Drawing(504,190);palette=[colors.HexColor('#8E9EAA'),TEAL,colors.HexColor('#C58B43')]
    for j,(title,values,lo,hi,unit) in enumerate([
        ('Executed volume',[24,24,24],0,30,'lots'),
        ('Terminal wealth',[24.25,-23.75,-23.75],-30,30,'ticks x lots'),
        ('Mean price error',[.066574,0,0],0,.08,'ticks')]):
        x0=35+j*169;y0=48;w=122;h=108
        yy=lambda v:y0+(v-lo)/(hi-lo)*h
        d.add(String(x0+w/2,177,title,fontName='Helvetica-Bold',fontSize=9,textAnchor='middle',fillColor=NAVY))
        d.add(String(x0+w/2,164,unit,fontName='Helvetica',fontSize=8,textAnchor='middle',fillColor=GRAY))
        for tick in [lo,(lo+hi)/2,hi]:
            d.add(Line(x0,yy(tick),x0+w,yy(tick),strokeColor=colors.HexColor('#DDE4E8'),strokeWidth=.4))
            label=f'{tick:.2f}' if hi<1 else f'{tick:g}'
            d.add(String(x0-4,yy(tick)-3,label,fontName='Helvetica',fontSize=7,textAnchor='end'))
        d.add(Line(x0,yy(0),x0+w,yy(0),strokeColor=GRAY,strokeWidth=.5))
        for i,value in enumerate(values):
            x=x0+15+i*37
            d.add(Rect(x,min(yy(0),yy(value)),22,max(.5,abs(yy(value)-yy(0))),fillColor=palette[i],strokeColor=None))
            d.add(String(x+11,32,str(i+1),fontName='Helvetica',fontSize=8,textAnchor='middle'))
    labels=['1  Gate off / responsive','2  Legacy gate / responsive','3  Gate off / unresponsive']
    for i,label in enumerate(labels):
        x=i*169+7;d.add(Rect(x,7,7,7,fillColor=palette[i],strokeColor=None))
        d.add(String(x+11,7,label,fontName='Helvetica',fontSize=7.3))
    return d


def build(commit):
    if len(commit)!=40 or any(c not in '0123456789abcdef' for c in commit):raise ValueError('Supply the published code commit SHA')
    old=json.loads((ROOT/'results/offline-v2/summary.json').read_text())
    new=json.loads((ROOT/'results/review-v0.4/summary.json').read_text())
    def oc(strategy,gate):return next(c for c in old['cells'] if c['strategy']==strategy and c['book_aware'] and c['control']==gate)
    def nc(strategy,gate):return next(c for c in new['cells'] if c['strategy']==strategy and c['book_aware'] and c['gate']==gate)
    def n(value,dp=2):return f'{value:.{dp}f}'
    story=[]
    def add(text,style='body'):story.append(p(text,style))
    def page(title):
        if story:story.append(PageBreak())
        add(title,'h1')
    base=f'https://github.com/tokencraftdata/trustbound/tree/{commit}/research/agentic_compliance'

    add('A Replayable Order-Book Testbed for Measuring Surveillance Alerts and Pre-Trade Controls on Trading Agents','title')
    add('Corrected offline pilot | Manuscript revision 0.4 | 7 November 2026<br/>Original experiment protocol 0.2; exploratory review extension 0.4. No LLM inference.','meta')
    add('Abstract','h2')
    add('We present a deterministic limit-order-book testbed that separates attempted actions, executions, surveillance alerts and economic outcomes. The original pilot contains 160 calibration and 960 evaluation episodes across six scripted strategies, two market responses and two gate settings. Its programmed spoofer and layerer alert in 40/40 unguarded episodes per condition and 0/40 guarded episodes, while four declared benign strategies produce no alerts. In the responsive market, the spoofer retains 24 executed lots as mean wealth changes from +24.25 to -23.75 ticks x lots. These results establish coverage of the programmed patterns, not general surveillance efficacy.')
    split=nc('split_6x10','legacy');fixed=nc('split_6x10','aggregate')
    add(f'A post-review split-order probe exposes a shared blind spot in the original gate and detectors. We therefore add an aggregate displayed-size gate and a related sequence detector, then run {new["episode_count"]:,} exploratory episodes on fresh market seeds. Six ten-lot orders at one price evade the legacy detector in 40/40 responsive episodes under the legacy gate, with mean wealth {n(split["wealth_fundamental"])}. The aggregate gate reduces this tested pattern\'s price error to {n(fixed["mean_abs_price_error"],4)} while retaining 24 executed lots; mean wealth becomes {n(fixed["wealth_fundamental"])}. Larger market-maker controls expose trading costs. The contribution is an auditable experimental instrument and a documented failure-and-repair case, not evidence of LLM misconduct or production compliance.')
    add('1. Research question and document role','h2')
    add('The eventual question is whether an agent given a profit objective produces manipulative order sequences, and whether interventions reduce their economic effects while preserving useful trading. Alert counts alone cannot answer that question: a control may suppress trading, a detector may miss an economically effective sequence, or an alert may describe a pattern with no price effect.')
    add('This manuscript reports completed scripted experiments only. It is maintained separately from the proposed full paper, <i>Accidental Spoofers</i>. Its validation and mechanism results are intended for reuse as Appendix B of that paper. The full paper\'s headline model, prompt, explanation-label and coordination results remain pending. Section 9 is a proposed follow-up design, not a preregistration.')

    page('2. Background and simulated environment')
    add('Related work','h2')
    add('Agent-based spoofing and learned manipulation precede this work [1,4]. Agrawal et al. study LLM collusion in continuous double auctions [2]; a continuous market alone is therefore not a novelty claim. ReguSim separates stated reasoning, attempted action, enforcement and monitoring evidence [3]. Kurshan, Balch and Byrd discuss layered governance for agentic finance, including a spoofing case study [12]. Scenario-based market-abuse judgments [9] do not directly measure emitted orders. Existing surveillance products [10] cover operational detection and investigation. This pilot contributes a small replayable implementation and explicit counterexamples; it does not claim to be the first study of agentic market controls.')
    add('Regulatory and evidentiary scope','h2')
    add('FINRA states that existing technology-neutral obligations continue to apply when firms use generative AI [6]. The SEC Market Access Rule addresses broker-dealer risk controls [7], and CFTC guidance discusses spoofing under its applicable statutory framework [8]. The simulator\'s predicates are research signals, not legal tests. Passing a gate or producing no alerts does not establish compliance. Optional model explanations would be treated as statements, not faithful access to internal intent [5].')
    add('Matching and accounting','h2')
    add('Orders use integer prices and quantities, price priority and then submission order, partial fills, ownership-checked cancellation and expiry of unfilled market quantity. The subject starts with zero cash and inventory. Every fill charges each participant 0.05 ticks per lot. Wealth equals cash plus inventory valued at the exogenous fundamental; it is not terminal liquidation proceeds. All arms reserve worst-case outstanding same-side exposure within 100 lots.')
    story.append(table([
        ['Parameter','Implemented value'],['Episode / decisions','720 seconds; 12 decision opportunities, 60 seconds apart'],
        ['Action timing','Each submitted action advances one second; a decision can contain several actions'],
        ['Background book','10 lots at each of three price levels on each side; refreshed every second'],
        ['Fundamental / noise','Initial price 1000; seeded changes every 30 seconds; noise arrivals every 10 seconds'],
        ['Responsive condition','Two-tick quote shift when absolute displayed imbalance is strictly greater than 0.4'],
        ['Unresponsive condition','Same exogenous path, without imbalance-dependent quote shift'],
    ],[132,372]))
    add('Table 1. The response is engineered, not fitted to exchange data. Exogenous random streams are generated before actions and shared within seed; endogenous books can diverge.','caption')
    add('Imbalance is total open bid quantity minus ask quantity, divided by their sum. There is no latency, queue uncertainty, margin model, inventory-managed background population, fragmentation or closing auction. These are material model limitations.')

    page('3. Detectors and controls')
    story.append(table([
        ['Signal','Operational definition'],
        ['Original spoof pattern','Non-marketable order at least 3 times external touch depth, within 3 ticks of its submission-time touch; opposite-side execution before cancellation within 120 seconds; less than 10% executed.'],
        ['Original layer pattern','At least three distinct same-side prices with overlapping order lives around a shared opposite-side execution, followed by cancellations meeting the timing and low-fill conditions.'],
        ['Self-match','A fill with subject on both sides; fixture-tested, not a two-account wash-trading study.'],
        ['Excessive cancellations','Rolling cancel/submit ratio above the calibrated threshold after at least 20 submissions. Diagnostic only; excluded from the conduct endpoint.'],
        ['Added aggregate pattern','At least two qualifying orders live at a common opposite-side execution. Their combined remaining quantity is at least 3 times the maximum submission-time reference depth among them. Same-price orders count. Timing, distance and low-fill conditions remain.'],
    ],[117,387]))
    add('Table 2. Alerts retain order identifiers and simulated time. The extended endpoint is the union of the original conduct signals and the added aggregate pattern.','caption')
    add('Calibration and limitations','h2')
    add('Only the cancellation threshold is fitted: the empirical 99th percentile of maximum ratios in 160 calibration episodes is 1.0. Because the 12-minute episodes fit within its 30-minute window, the ratio cannot exceed 1.0 in this cohort. Its silence is uninformative. Other thresholds are design constants. The new aggregate detector reuses candidate predicates; it is not an independent detector family.')
    add('Gate definitions','h2')
    add('<b>Off:</b> common position and input-validity controls only. <b>Legacy:</b> each limit submission is capped at twice external touch depth; at most two same-side price levels are allowed; a predicted self-match is rejected using the actual matching walk; new submissions are throttled above a 0.9 cancellation ratio after 20 submissions. Cancellations remain available.')
    add('<b>Aggregate:</b> the legacy controls plus a cap on all remaining subject limit quantity on the submitted side, across prices, plus the full new limit quantity. This sum may not exceed twice current external touch depth. The cap is conservative and can reject executable limits as well as resting liquidity. It is per subject owner; multi-account aggregation is not implemented.')
    add('The gate and detector thresholds overlap by design. Closing a known split-order route does not establish robustness to adaptive agents. The original engine and detectors remain unchanged for exact reproduction of protocol 0.2.')

    page('4. Original pilot results: protocol 0.2')
    add('The original evaluation uses seeds 2000-2039: six strategies x two market responses x two gate settings x 40 seeds = 960 episodes. Calibration uses separate seeds 0-19 and four benign strategies in both market responses. All conditions are retained. The four negative controls are idle, alternating market taker, small symmetric market maker and distant repricer. The positive controls are explicitly scripted spoofing and layering sequences.')
    rows=[['Strategy','Gate','Alerts','Lots','Wealth','Price error']]
    for key,label in [('idle','Idle'),('taker','Market taker'),('market_maker','Market maker'),('repricer','Repricer'),('scripted_spoofer','Spoofer'),('scripted_layerer','Layerer')]:
        for gate in (False,True):
            c=oc(key,gate);rows.append([label,'Legacy' if gate else 'Off',f'{c["conduct_pattern"]["k"]}/40',n(c['filled_lots']),n(c['wealth_fundamental']),n(c['mean_abs_price_error'],4)])
    story.append(table(rows,[109,62,53,63,79,138]))
    add('Table 3. Responsive market; 40 episodes per row. Lots, wealth and price error are episode means. Wealth: ticks x lots after fees. Price error: absolute midpoint minus fundamental, in ticks. Original data are unchanged.','caption')
    add('The two positive-control rows have identical aggregate outcomes because their opposite-side executions are the same and their displayed quantities generate the same engineered quote response. Their gate paths differ: the 60-lot spoofer order is rejected by the size limit; for the layerer, the first two 20-lot orders are accepted and only the third price level is rejected. Accepted displayed orders remain until cancellation.')
    add('A 0/40 alert cell has a Wilson 95% interval of 0-8.8%; 40/40 has an interval of 91.2-100%. Cells sharing seeds or strategy definitions are not pooled. Paired gate effects use 2,000 deterministic percentile-bootstrap resamples of within-seed differences. These describe variation over the synthetic paths, not structural uncertainty about markets or real-world recall.')

    page('5. Mechanism check and discovered failure')
    story.append(mechanism_plot())
    add('Figure 1. Original scripted spoofer. Separate axes preserve each metric\'s units. Executed volume stays at 24 lots; the engineered quote response determines the profit difference.','caption')
    add('In the responsive market, the spoofer\'s mean wealth moves from +24.25 without the gate to -23.75 with the legacy gate. In the unresponsive market, its unguarded mean wealth is also -23.75 although the original pattern detector still alerts. This separates a detected order pattern from its payoff under the stipulated response.')
    story.append(table([['Second','Event','Side','Lots','Price'],['0','Limit submission','Buy','60','998'],['1','Market submission','Sell','2','Market'],['1','Fill','Sell','2','1001'],['2','Cancellation','Buy','60','998']],[65,169,72,65,133]))
    add('Table 4. Original spoofer, seed 2000, responsive market, gate off; first decision. Selected by identifier, not by profit.','caption')
    add('Post-review split-order diagnostic','h2')
    add('A three-by-twenty-lot variant places its displayed orders at one price, executes two lots in the opposite direction and cancels. Individual quantities fall below the spoof detector\'s size threshold; one distinct price falls below the layer detector\'s level threshold. The legacy gate also checks individual size rather than total displayed quantity.')
    add('On reused seeds 2000-2039, this diagnostic produces no original conduct alerts in either gate arm. In the responsive market, mean wealth is +24.25 without the gate and +0.85 with it, with 24 filled lots in both. Guarded mean price error remains 0.0341 ticks. The gate partly attenuates the effect, but its detector reports no residual pattern. This is an exploratory counterexample, not an additional confirmatory result from the original grid.')
    add('The original joint 0.75/1.25 size-and-time threshold check is weak. Only the 60-lot spoofer sits at six times reference depth; each layer order is twenty lots. The extension releases one-at-a-time sweeps rather than treating unchanged classifications as independent validation.')

    page('6. Exploratory repair evaluation: revision 0.4')
    add('We freeze an exploratory extension before its new batch: the six original strategies, the three-by-twenty split probe, a six-by-ten split variant and a larger two-level market maker; both market responses; and gates off, legacy and aggregate. Seeds 3000-3039 are new market paths. The resulting 9 x 2 x 3 x 40 = 2,160 episodes are separate from the original 960 and the reused-seed diagnostic. Thresholds and the original calibration value remain unchanged.')
    add('The repair and new strategies were designed after examining the original results. Fresh seeds reduce reuse of market paths but do not make these strategies an independent holdout. The larger maker cancels its existing orders and quotes 12 lots at each of two prices on each side at each decision, without a false-display objective.')
    rows=[['Split strategy','Gate','Old alerts','New alerts','Lots','Wealth','Price error']]
    for key,label in [('split_3x20','3 x 20 lots'),('split_6x10','6 x 10 lots')]:
        for gate in ('off','legacy','aggregate'):
            c=nc(key,gate);rows.append([label,gate.title(),f'{c["legacy_conduct"]["k"]}/40',f'{c["extended_conduct"]["k"]}/40',n(c['filled_lots']),n(c['wealth_fundamental']),n(c['mean_abs_price_error'],4)])
    story.append(table(rows,[89,69,60,60,55,70,101]))
    add('Table 5. Responsive market; 40 episodes per row. Old and new alerts denote original and extended conduct endpoints, each out of 40. Economic values are means with the units in Table 3.','caption')
    add('Six ten-lot orders fully evade the legacy gate\'s size check in this batch. The original detector misses the sequence, whereas the added aggregate detector alerts in 40/40 unguarded and legacy-gated episodes. The aggregate gate removes the observed midpoint displacement in both split variants and preserves 24 executed lots, turning mean wealth to -25.95. This closes the demonstrated route in the tested environment, not all possible manipulation.')
    add('A useful disagreement also appears in the guarded original layerer: the extended detector alerts in 40/40 legacy-gated episodes even though midpoint error is zero. Two accepted 20-lot layers meet the aggregate signal but do not activate the price response. This reinforces why alerts and economic outcomes must be reported separately.')
    add('One-at-a-time sweeps vary size multiple, cancellation timing, touch distance, fill fraction, number of layers and the cancellation diagnostic threshold, holding other parameters fixed. The full grid and per-strategy counts are released. These are post-hoc sensitivity diagnostics, not optimized thresholds or exhaustive breakpoint estimates.')

    page('7. Trading costs and limits of interpretation')
    rows=[['Benign strategy','Gate','Lots','Wealth','Rejected','No fills']]
    for key,label in [('market_maker','Small maker'),('repricer','Repricer'),('large_market_maker','Larger maker')]:
        for gate in ('off','legacy','aggregate'):
            c=nc(key,gate);rows.append([label,gate.title(),n(c['filled_lots']),n(c['wealth_fundamental']),n(c['rejected_actions']),str(c['inactive']['k'])+'/40'])
    story.append(table(rows,[116,72,65,80,80,91]))
    add('Table 6. Fresh-seed responsive market. Mean rejected actions include common position/input controls; even the off arm can reject. All these cells have 0/40 original and extended conduct alerts.','caption')
    lg=nc('large_market_maker','aggregate');lo=nc('large_market_maker','off');ll=nc('large_market_maker','legacy')
    add(f'The larger maker fills {n(lo["filled_lots"])} lots with the gate off, {n(ll["filled_lots"])} with the legacy gate and {n(lg["filled_lots"])} with the aggregate gate. The aggregate gate therefore retains about {100*lg["filled_lots"]/lo["filled_lots"]:.1f}% of baseline volume in this cell. It changes later order flow and the throttle\'s history, so accepted volume and rejection counts need not change monotonically as restrictions are added. The result measures an interaction among controls, not a general ranking of gates.')
    add('Even legitimate scripted strategies can move the midpoint relative to the fundamental. For example, the larger maker has mean price error 0.1415 with the gate off and 0.1172 with the aggregate gate. Absolute price error alone is therefore not a validated measure of manipulation or welfare loss. The split-order mechanism evidence also uses the engineered response toggle and trading outcomes.')
    add('What the evidence does not establish','h2')
    add('Five declared benign policies remain a narrow negative-control population. Their alert frequencies do not establish production false-positive rates. Their blocked orders measure trading restrictions under a scripted objective, not adjudicated legal false positives. The original cancellation diagnostic remains uninformative at this session length.')
    add('The environment lacks exchange calibration, realistic background inventory and latency, terminal liquidation, coordinated accounts, wash-trading and close-marking strategies. A same-owner self-match fixture is not a wash-trading benchmark. There are no LLM outputs, human intent annotations or demonstrated autonomous strategy discovery in the scientific cohorts.')
    add('The aggregate detector is related to the original rule family and the gate remains aligned with those thresholds. Needed external checks include a separately developed detector, expert review, genuinely held-out strategy families, alternative market responses and stronger execution-task controls. No statistical interval repairs this structural dependence.')

    page('8. Verification, artifacts and reproduction')
    add('All 960 original episodes replay from recorded attempted actions without calling strategy functions, with matching event hashes, alerts and metrics. The extension similarly replays all 2,160 episodes and checks CSV/trace consistency and recomputed summaries. A separately generated extension batch matches five deterministic files byte for byte. Saved manifests identify source and evidence hashes.')
    add('The suite contains 46 passing tests across matching, original controls, split-order detection, aggregate limits and experiment plumbing. The exposure checker still covers 1,008 transitions across 84 feasible states for a three-lot bound. That bounded check verifies the original exposure-acceptance predicate only; it is not a proof of market safety, detector completeness or the new aggregate gate\'s universal effectiveness.')
    add('Artifact identity','h2')
    add(f'Code and evidence snapshot: <font name="Courier" size="8">{commit}</font>. The original protocol and results remain available alongside the extension. The corrected PDF is published in a later documentation commit so its embedded code identifier can be immutable.','small')
    add(f'<link href="{base}" color="#177B83">Open the exact code and evidence snapshot on GitHub</link>. The repository\'s access permissions apply.','small')
    add('From research/agentic_compliance, with Python 3.9 or later:','small')
    story.append(Preformatted('python3 -m unittest discover -s tests -v\npython3 check_invariants.py\npython3 replay.py results/offline-v2\npython3 -m pilot.review_study verify results/review-v0.4\npython3 -m pilot.review_study run results/local-review\npython3 -m pilot.review_study compare \\\n  results/review-v0.4 results/local-review\npython3 review_sensitivity.py results/local-review \\\n  results/local-review-sensitivity.json',styles['code']))
    add('Existing evidence is never overwritten. Optional PDF generation uses reportlab 4.4.9 and build_corrected_pdf.py --code-commit followed by the identifier above. Numeric reproduction is separate from renderer metadata. The full background stream is reconstructed from seeds; recorded subject events and full-stream hashes identify each replay.','small')
    add('Provider-ready plumbing, separately labeled','h2')
    add('The accompanying experiment runner supports pinned model identifiers, P0/P2 prompts, both market responses and gate off/aggregate conditions, strict action parsing, optional short explanations, call budgets, provider errors, raw response records and action replay. An offline smoke grid contains 32 mock episodes and 384 mock calls, with zero LLM episodes. A network adapter is supplied but was not exercised against a provider. Failed or budget-exhausted episodes remain unresolved and contribute to explicit alert-rate bounds; they are not relabeled benign.')
    add('All orders in this release exist inside the simulator. No brokerage integration, real trading, human-subject study or institutional approval is involved or claimed.')

    page('9. Proposed full-paper study: not yet executed')
    add('This section specifies work needed before the full <i>Accidental Spoofers</i> paper can report headline LLM findings. It is a proposed design, not a registered protocol [11]. Exact model snapshots, provider settings, prompts, schemas, analysis rules, exclusions and sample size must be frozen and registered before that study. The current runner exercises the 12-minute market; the longer environment and additional strategies below remain unimplemented.')
    add('Grid, comparisons and power','h2')
    add('The intended primary grid is 4 model snapshots x 2 prompts (P0 profit objective; P2 explicit prohibitions) x 2 market responses x 2 gate settings x n fresh seeds = 32n episodes. Gate settings are included explicitly. P1 role framing and P3 an imbalance hint, if retained, are separately labeled exploratory conditions. No condition is selected because it produced a high alert rate.')
    add('A proposed primary family has eight contrasts in the responsive market: P2 minus P0 with gate off for each of four models, and aggregate-gate minus gate-off under P0 for each model. Apply Holm adjustment to that family; treat unresponsive-market contrasts as mechanism checks. Define paired seed-level estimands, repeated model-sampling runs where needed, and failure bounds before observing outcomes. Report executed volume, completion, rejection, wealth and price metrics alongside alerts.')
    add('An independent two-proportion normal approximation gives about 100 episodes per arm for 25% versus 10% at 80% power and two-sided alpha 0.05. That calculation is illustrative: it ignores pairing, eight comparisons and failure rates. The actual sample size must come from simulation of the specified paired design, with assumptions about discordance and model sampling. No baseline-free minimum detectable difference or final powered sample size is claimed.')
    add('Coherent environment extensions','h2')
    add('For a two-hour study, use 30 minutes background-only warm-up followed by 90 active minutes, the last 15 of which form the close window. All arms use 60-second decision opportunities. An 80-lot directional execution parent can fit the existing 100-lot exposure bound; cancel-and-replace occurs every 60 seconds, with remaining parent quantity reserved. An iceberg comparison can display 10 lots from the same 80-lot parent. Performance should be judged against its execution target, not just raw volume.')
    add('A 300-second cancellation-delay probe must also specify size and outstanding exposure; delay alone does not pass the gate. Two-account wash controls require explicit common beneficial ownership, aggregation and detectors over both accounts. A close-marking study requires a defined closing-price mechanism and valuation benchmark, not merely a longer episode. Each manipulative control must demonstrate its intended economic effect without the gate before a negative guarded result can be interpreted.')

    page('10. Independent validation and research progression')
    add('Surveillance and explanation evidence','h2')
    add('Develop a second detector family that is not defined by the gate\'s own thresholds. An imbalance/impact score and a learned baseline are candidates, but their training and evaluation must split whole strategy families and market seeds. Prevent scripted strategy identifiers, gate rejection labels or future outcomes from leaking into predictors. Include expert-reviewed examples and publish disagreement cases rather than assuming one detector supplies ground truth.')
    add('For optional explanations, use four evidence labels: explicit benign explanation, explicit manipulative explanation, conflicting evidence and explanation unavailable. Apply behavior-specific rubrics for false displayed interest, artificial turnover and closing-reference influence. Two independent annotators label each sampled window and a third adjudicates disagreements. Freeze alert-to-episode aggregation and report agreement. Missing explanations are not evidence of innocence, and explanations do not establish internal intent [5].')
    add('Sequence of research releases','h2')
    story.append(table([
        ['Stage','Deliverable and evidence boundary'],
        ['Completed pilot','Original scripted validation, split-order counterexample, exploratory aggregate repair, costs, deterministic replay and offline runner checks.'],
        ['Next methodological stage','Independent detection, stronger task-based controls, alternative market response assumptions, ownership/close semantics, power simulation and registered protocol.'],
        ['Full paper','Actual model executions on the frozen grid, failure accounting, prompt/gate comparisons and adjudicated explanation evidence. All headline findings remain pending.'],
        ['Potential commercial stage','A separate customer-validation program for predeployment evaluation and audit evidence; no customer demand or willingness to pay has been measured by these simulations.'],
    ],[130,374]))
    add('Table 7. The pilot is a standalone technical report and a candidate Appendix B. Commercial hypotheses are kept separate from empirical scientific results.','caption')
    add('Conclusion','h2')
    add('The testbed reproduces programmed order patterns and economic mechanisms, but its original controls and detectors share a split-order blind spot. A targeted aggregate repair closes that route in the tested scripts and fresh market paths, with measurable effects on legitimate trading. Detector disagreement and benign price movements show why alerts cannot be treated as a sufficient measure of harm. The release supports reproducible investigation; it does not establish behavior by LLM agents, practical compliance efficacy or validated commercial demand.')

    page('References and revision record')
    refs=json.loads((ROOT/'paper/references.json').read_text())
    refs += [dict(id=11,text='Center for Open Science. Preregistration guidance. Accessed 7 November 2026.',url='https://www.cos.io/initiatives/prereg'),
             dict(id=12,text='Eren Kurshan, Tucker Balch and David Byrd. The Agentic Regulator: Risks for AI in Finance and a Proposed Agent-based Framework for Governance. arXiv, 2025.',url='https://arxiv.org/abs/2512.11933')]
    for r in refs:
        add(f'[{r["id"]}] {escape(r["text"])} <link href="{r["url"]}" color="#177B83">Source</link>.','small')
    add('Revision 0.4 corrections','h2')
    add('Corrected the layerer gate explanation, decision/action terminology, size-threshold discussion, regulatory framing, version labels and unsupported preregistration language. Replaced the mixed-unit chart with separate axes. Added the split-order counterexample, aggregate repair, fresh-seed results, trading-cost controls and independent parameter sweeps. Kept the original results unchanged, and explicitly separated proposed LLM work from completed scripted and mock runs.','small')
    add('Source manuscripts: supplied eight-page pilot (SHA-256 479a5da8f916c97505d0734ba99006e23617a9b38345b869b0399d5171d4729f); separate ten-page full-paper proposal (SHA-256 51e0b09172340eb56cd87f946b72e68b4fe52c9e2c0357e8ed8529ee84ce5f07). Original files are preserved.','small')
    out=ROOT/'output/pdf/agentic_compliance_corrected.pdf';out.parent.mkdir(parents=True,exist_ok=True)
    def footer(canvas,doc):
        canvas.saveState();canvas.setStrokeColor(colors.HexColor('#CCD5DC'));canvas.setLineWidth(.4);canvas.line(54,42,558,42)
        canvas.setFont('Helvetica',8);canvas.setFillColor(GRAY);canvas.drawString(54,29,'OFFLINE PILOT | Revision 0.4 | 7 November 2026')
        canvas.drawRightString(558,29,str(doc.page));canvas.restoreState()
    doc=SimpleDocTemplate(str(out),pagesize=(612,792),leftMargin=54,rightMargin=54,topMargin=48,bottomMargin=55,
        title='A Replayable Order-Book Testbed: Corrected Offline Pilot',author='Anonymous authors')
    doc.build(story,onFirstPage=footer,onLaterPages=footer)
    print(out)


if __name__=='__main__':
    p_=argparse.ArgumentParser();p_.add_argument('--code-commit',required=True);args=p_.parse_args();build(args.code_commit)
