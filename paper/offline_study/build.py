"""Build the offline paper and standalone figures from saved measurements.

Usage: python paper/offline_study/build.py --code-commit <immutable SHA>
Dependencies: requirements.txt in this directory. Numeric reproduction is stdlib.
"""
import argparse
import hashlib
import json
import platform
from functools import partial
from pathlib import Path
from xml.sax.saxutils import escape

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import reportlab
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, Image, KeepTogether

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
DATA = ROOT / 'results/paper-review-v1'
TITLE = 'Order Splitting, Cancellation Timing, and the Limits of Trading Controls'
SUBTITLE = 'A replayable offline study of surveillance and pre-trade gates'
REFS = [
    ('Xintong Wang and Michael P. Wellman. Spoofing the Limit Order Book: An Agent-Based Model. AAMAS, 2017, pp. 651-659.', 'https://www.ifaamas.org/Proceedings/aamas2017/pdfs/p651.pdf'),
    ('David Byrd. Learning Not to Spoof. arXiv:2306.06087, 2023. Related ICAIF publication, 2022.', 'https://arxiv.org/abs/2306.06087'),
    ('Xintong Wang and Michael P. Wellman. Market Manipulation: An Adversarial Learning Framework for Detection and Evasion. IJCAI, 2020, pp. 4626-4632.', 'https://doi.org/10.24963/ijcai.2020/638'),
    ('David Byrd, Maria Hybinette, and Tucker Hybinette Balch. ABIDES: Towards High-Fidelity Market Simulation for AI Research. arXiv:1904.12066, 2019.', 'https://arxiv.org/abs/1904.12066'),
    ('Eren Kurshan, Tucker Balch, and David Byrd. The Agentic Regulator: Risks for AI in Finance and a Proposed Agent-based Framework for Governance. arXiv:2512.11933, 2025.', 'https://arxiv.org/abs/2512.11933'),
    ('FINRA. Regulatory Notice 24-09: Artificial Intelligence and Large Language Models. June 27, 2024.', 'https://www.finra.org/rules-guidance/notices/24-09')
]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def figures(summary):
    folder = HERE / 'figures'; folder.mkdir(exist_ok=True)
    plt.rcParams.update({'font.family': 'DejaVu Sans', 'font.size': 9, 'svg.fonttype': 'none'})
    cells = summary['historical']['throttle-isolation-v0.4']['cells']
    order = ['off', 'legacy', 'aggregate', 'aggregate_throttle_off']
    rows = [next(c for c in cells if c['strategy'] == 'large_market_maker' and c['gate'] == gate) for gate in order]
    fig, axes = plt.subplots(1, 2, figsize=(6.6, 2.35), layout='constrained')
    labels = ['No gate', 'Legacy', 'Aggregate', 'Aggregate\nno throttle']
    for ax, key, title in zip(axes, ['filled_lots', 'throttle_rejections'], ['Executed lots', 'Throttle rejections']):
        ax.bar(range(4), [r[key] for r in rows], color=['#9aa5b1', '#aa5b4a', '#285675', '#387c6e'])
        ax.set_xticks(range(4), labels, fontsize=7)
        ax.set_ylabel(title); ax.spines[['top', 'right']].set_visible(False)
        ax.set_ylim(0, max(r[key] for r in rows) * 1.2)
        for i, r in enumerate(rows): ax.text(i, r[key] + 0.6, f'{r[key]:.1f}', ha='center', fontsize=8)
    fig.savefig(folder/'throttle.png', dpi=240); fig.savefig(folder/'throttle.svg'); plt.close(fig)
    ws = [120, 180, 240, 300, 420, 600]
    rs = [c for c in summary['cadence_cells'] if c['gate'] == 'off']
    values = np.array([[c['window_alerts'][str(w)] for w in ws] for c in rs])
    fig, ax = plt.subplots(figsize=(6.6, 2.65), layout='constrained')
    im = ax.imshow(values, vmin=0, vmax=40, cmap='YlOrRd', aspect='auto')
    ax.set_xticks(range(len(ws)), [str(w) for w in ws]); ax.set_xlabel('Shared detection window (seconds)')
    ax.set_yticks(range(len(rs)), [str(r['cadence']) for r in rs]); ax.set_ylabel('Maker re-quote cadence (seconds)')
    for i in range(len(rs)):
        for j in range(len(ws)):
            ax.text(j, i, f'{values[i,j]}/40', ha='center', va='center', color='white' if values[i,j] >= 26 else '#202020')
    fig.colorbar(im, ax=ax, label='Episodes alerted', ticks=[0, 10, 20, 30, 40])
    fig.savefig(folder/'cadence.png', dpi=240); fig.savefig(folder/'cadence.svg'); plt.close(fig)
    return folder


def build(commit):
    # Use distributable outline fonts shipped with the pinned matplotlib build.
    # Base-14 PDF fonts are not embedded by ReportLab and do not meet arXiv's
    # PDF-only submission guidance.
    font_dir = Path(matplotlib.get_data_path()) / 'fonts/ttf'
    for name, filename in [('StudySerif', 'STIXGeneral.ttf'),
                           ('StudySerif-Bold', 'STIXGeneralBol.ttf'),
                           ('StudySerif-Italic', 'STIXGeneralItalic.ttf'),
                           ('StudySerif-BoldItalic', 'STIXGeneralBolIta.ttf'),
                           ('StudyMono', 'DejaVuSansMono.ttf')]:
        pdfmetrics.registerFont(TTFont(name, str(font_dir / filename)))
    pdfmetrics.registerFontFamily('StudySerif', normal='StudySerif',
                                 bold='StudySerif-Bold', italic='StudySerif-Italic',
                                 boldItalic='StudySerif-BoldItalic')
    summary = json.loads((DATA/'summary.json').read_text())
    figs = figures(summary)
    out = ROOT/'output/pdf/order_splitting_and_timing.pdf'; out.parent.mkdir(parents=True, exist_ok=True)
    width = 6.75*inch
    styles = {
        'body': ParagraphStyle('body', fontName='StudySerif', fontSize=10.5, leading=14, spaceAfter=8, alignment=TA_JUSTIFY),
        'small': ParagraphStyle('small', fontName='StudySerif', fontSize=9, leading=11.5, spaceAfter=7),
        'caption': ParagraphStyle('caption', fontName='StudySerif', fontSize=9, leading=11.5, spaceAfter=10),
        'title': ParagraphStyle('title', fontName='StudySerif-Bold', fontSize=20, leading=24, alignment=TA_CENTER, spaceAfter=12),
        'subtitle': ParagraphStyle('subtitle', fontName='StudySerif', fontSize=12, leading=16, alignment=TA_CENTER, spaceAfter=10),
        'h1': ParagraphStyle('h1', fontName='StudySerif-Bold', fontSize=13, leading=16, spaceBefore=5, spaceAfter=10, keepWithNext=True),
        'h2': ParagraphStyle('h2', fontName='StudySerif-Bold', fontSize=11, leading=14, spaceBefore=6, spaceAfter=6, keepWithNext=True),
        'cell': ParagraphStyle('cell', fontName='StudySerif', fontSize=8.6, leading=11),
        'code': ParagraphStyle('code', fontName='StudyMono', fontSize=8, leading=11, spaceAfter=6),
    }
    story, source = [], []
    def p(text, style='body'):
        story.append(Paragraph(text, styles[style])); source.append({'type': style, 'text': text})
    def page(): story.append(PageBreak()); source.append({'type':'page_break'})
    def table(headers, rows, fractions):
        data = [[Paragraph('<b>'+escape(str(x))+'</b>', styles['cell']) for x in headers]]
        data += [[Paragraph(escape(str(x)), styles['cell']) for x in row] for row in rows]
        t = Table(data, colWidths=[width*x for x in fractions], repeatRows=1, hAlign='LEFT')
        t.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('LINEABOVE',(0,0),(-1,0),0.7,colors.black),
             ('LINEBELOW',(0,0),(-1,0),0.4,colors.black),('LINEBELOW',(0,-1),(-1,-1),0.6,colors.black),
             ('LEFTPADDING',(0,0),(-1,-1),4),('RIGHTPADDING',(0,0),(-1,-1),4),
             ('TOPPADDING',(0,0),(-1,-1),5),('BOTTOMPADDING',(0,0),(-1,-1),5)]))
        story.extend([t,Spacer(1,8)]);source.append({'type':'table','headers':headers,'rows':rows})
    def figure(name, height, caption):
        story.append(Image(str(figs/name), width=width, height=height*inch));p(caption,'caption')
    def cell(name, **kw):
        return next(c for c in summary['historical'][name]['cells'] if all(c[k] == v for k,v in kw.items()))
    def n(x):return f'{x:.2f}'
    p(TITLE,'title');p(SUBTITLE,'subtitle')
    p('Sandip Nandi<br/>Independent Researcher','subtitle')
    p('9 November 2026 | Preprint version 1.2','subtitle')
    p('Abstract','h1')
    p('An order-level control can reject a conspicuous spoof while admitting the same displayed quantity split across smaller orders. We study this failure in a deterministic, replayable limit-order-book simulator. An initial 960-episode scripted evaluation is followed by 2,160 repair episodes, throttle-isolation and randomized-parameter probes, and a new 360-episode measurement study. Six 10-lot orders bypass both a legacy per-order gate and its original detectors in all 40 tested seeds. An aggregate gate removes the engineered response benefit for this probe, but its apparent advantage over the legacy gate on legitimate trading is largely a cancellation-throttle interaction. Matching responsive and unresponsive markets at the same gate finds no response-dependent wealth advantage in 40 randomized draws. Detection remains sensitive to legitimate quoting cadence: a 180-second window flags a patient market maker in 34 of 40 episodes when it re-quotes every 180 seconds, despite zero alerts at a 300-second cadence. These are exploratory results in a stipulated market mechanism. No LLM policy, live market, or human intent annotation is evaluated.')
    p('1. Introduction','h1')
    p('Surveillance and pre-trade controls answer different questions. Surveillance identifies an order sequence worth reviewing; a pre-trade gate decides whether a proposed action may enter the market. A clean alert count does not establish that a gate prevents profitable manipulation. A high rejection count does not establish that the remaining orders are harmless. Both measurements need an economic comparison and a legitimate trading task.')
    p('Order splitting means dividing a larger order into smaller submissions. It is not, by itself, evidence of deceptive conduct. The counterexample studied here displays the split quantity, executes a small trade on the opposite side, and cancels the remaining display. The scheduled execution controls instead use smaller orders to acquire an 80-lot position.')
    p('The experiments here began as infrastructure for a study of LLM trading agents. Scripted controls exposed problems before any model was run. Dividing a large order into smaller orders defeated a gate and detector that appeared effective against the original spoofer. Adding an aggregate quantity check repaired that case. Subsequent tests showed that the gate interacted with a submission throttle and that a detection window selected on one set of legitimate strategies failed when a maker changed its quoting cadence.')
    p('Our contribution is a reproducible account of these failures and their measurement. The artifact records attempted actions, accepted orders, rejection reasons, fills, detector outputs, economic outcomes, and replay checks. The study evaluates a particular testbed and several control designs. It does not estimate the prevalence of manipulation by LLMs, infer legal intent, or establish production readiness.')

    page();p('2. Setting and scope','h1')
    p('2.1. Relation to previous work','h2')
    p('Wang and Wellman [1] model spoofing against traders that use order-book information. Byrd [2] shows that a profit-seeking reinforcement-learning trader can discover spoofing and studies a learned recognizer used to discourage it. Wang and Wellman [3] explicitly study adversarial adaptation between manipulation and detection. Our parameterized scripts neither learn nor optimize against the detector. Their role is to expose implementation and evaluation weaknesses in a controlled setting.')
    p('ABIDES [4] offers a richer simulation architecture with agents and communication latencies. The engine used here is a much smaller, purpose-built model; it is not an ABIDES calibration. Kurshan, Balch, and Byrd [5] discuss agent-based governance and a spoofing case study. We therefore make no claim to introduce agent-aware financial controls. The contribution lies in the measured counterexamples, matched comparisons, and replayable evidence.')
    p('2.2. Market mechanics','h2')
    table(['Quantity','Implemented value'],[
        ['Session / decision spacing','720 seconds / 60 seconds; no warm-up'],
        ['Matching','Single instrument, integer ticks, price/time priority, partial fills'],
        ['Exposure bound','Absolute worst-case position commitment no greater than 100 lots'],
        ['Background quotes','10 lots at each of three price levels on each side'],
        ['Fundamental','Initial 1,000 ticks; increment sampled from {-1, 0, 0, 1} every 30 seconds'],
        ['Noise flow','A pre-generated 0, 1, or 2-lot market order opportunity every 10 seconds'],
        ['Fees','0.05 tick-lots per filled lot per participant']
    ],[.28,.72])
    p('Each accepted or rejected subject action consumes one simulated second. Background quotes are cancelled and replaced every second, including between actions within a decision. Unfilled market quantity expires. Subject cancellations remain available under every gate. The exogenous fundamental and noise streams are generated before the subject acts, so comparisons within a seed share the same external random inputs.')
    p('In the responsive condition, the background quotes shift by two ticks in the direction of displayed imbalance when |(B-A)/(B+A)| exceeds 0.4; B and A sum the resting quantities on each side of the book. The unresponsive condition omits this shift. This rule deliberately creates an exploitable response. It is not fitted to exchange data and is not a model of strategic equilibrium. Subject quotes can also move the midpoint without activating the rule, which matters when interpreting price error.')

    page();p('3. Controls, detectors, and outcomes','h1')
    p('3.1. Gates','h2')
    p('Every arm enforces the 100-lot position-commitment bound. The legacy gate also caps each limit submission at twice the current external reference depth, permits at most two resting price levels per side, rejects a predicted self-match using the matching engine\'s price/time walk, and throttles new submissions when cancellations divided by accepted submissions exceeds 0.9 after at least 20 accepted submissions in the window. External reference depth is the median of the two external touch quantities, excluding subject orders.')
    p('The aggregate gate retains those checks and caps the sum of remaining same-side subject limit quantity plus the full new quantity at twice reference depth. This is conservative: even a marketable limit is checked at its full submitted size. The check occurs at submission; it does not continuously rebalance orders when external depth changes. The throttle-off arm disables only the aggregate gate\'s submission throttle. The throttle window is 1,800 seconds, longer than every session studied here.')
    p('3.2. Pattern detectors','h2')
    p('The individual spoof pattern requires a nonmarketable order of at least three times submission-time reference depth, within three ticks of touch, less than 10% filled, an intervening opposite-side subject fill, and cancellation within the detection window. The layer pattern requires qualifying cancelled orders at at least three distinct levels sharing an opposite-side fill. Each of the three levels must enter the cancelled-order candidate set.')
    p('The aggregate pattern combines at least two qualifying orders live at a shared opposite-side fill, using their remaining quantities at that fill and the maximum of their submission-time reference depths. Self-matches are reported separately. A cancellation-ratio diagnostic is excluded from the conduct-alert union; its historical threshold of 1.0 is uninformative at this session length. No close-marking or cross-account wash-trading detector is implemented. The spoof, layer, and aggregate patterns share the same window parameter: changing it from 120 to 180 seconds changes all three.')
    p('3.3. Economic and operational outcomes','h2')
    p('Terminal wealth is cash plus inventory valued at the terminal fundamental, after fees. We also report filled lots, rejection reasons, inactivity, and mean absolute midpoint-minus-fundamental error. Price error is a diagnostic, not an abuse or welfare label: legitimate makers can generate it. Economic comparisons use the same seed. We do not interpret wealth differences between seed cohorts as strategy effects.')
    p('<b>Response-dependent wealth advantage:</b> D(g,d) = W(responsive, g, d) - W(unresponsive, g, d), where g is the gate and d fixes both the parameter draw and market seed. A positive D under the aggregate gate with no conduct alert is reported even if some orders were rejected. This replaces the historical summary\'s absolute-wealth and zero-rejection definition. The comparison isolates the response toggle within this model; it is not a legal definition of manipulation.')

    page();p('4. Experimental design and provenance','h1')
    table(['Study','Episodes','Seeds / scope'],[
        ['Initial scripted evaluation','960','2000-2039; 6 strategies x 2 responses x 2 gates'],
        ['Split-order repair','2,160','3000-3039; 9 strategies x 2 responses x 3 gates'],
        ['Throttle isolation','480','4000-4039; 3 strategies x 4 gates; responsive'],
        ['Parameterized evasion, 120 s window','120','40 draws; one seed each, 5000-5039; 3 conditions'],
        ['Same evasion draws, 180 s window','120','Same draws and paths; changed detector window'],
        ['Legitimate negative controls','400','6000-6039; 5 strategies x 2 gates; responsive'],
        ['New matched gated references','40','Same 40 evasion draws; aggregate, unresponsive'],
        ['New maker cadence grid','320','6000-6039; 4 cadences x 2 gates; responsive']
    ],[.42,.13,.45])
    p('Table 1. Evidence cohorts. These rows sum to 4,600 stored simulation episodes, not 4,600 independent cases. The 120-second and 180-second evasion cohorts repeat the same trajectories. The new 300-second maker cells repeat the existing control at a different scoring configuration. A separate 160-episode calibration and 32-episode mock-provider check are excluded from this total. The earlier exploratory discovery probe is also excluded.','caption')
    p('The initial strategies are idle, an alternating small market taker, a two-lot symmetric maker, a distant repricer, a programmed spoofer, and a programmed layerer. The repair study adds same-price splits of three 20-lot orders and six 10-lot orders, plus a legitimate maker quoting 12 lots at two levels on each side. Scripted spoof and split sequences submit displayed interest, make a two-lot opposite-side market trade, and cancel the remaining displayed orders.')
    p('The evasion generator samples order count 2-8, total size 40/60/80, price spread 0-5 ticks, submission gap 0-30 seconds, full/staggered/disjoint overlap, and execution delay 0-90 seconds. It assigns one market seed to each of 40 parameter vectors. This is a randomized parameter sample within one authored strategy family, not independent strategy-family validation. The manifest records RNG seed 20261108 and the exact draws. The repository first records the generator, draws, and results together; we do not claim that Git establishes a pre-run commitment or external preregistration.')
    p('The new cadence grid uses a maker quoting 16 lots at each of two levels on each side. It cancels and re-quotes every 120, 180, 240, or 300 seconds. Every cell uses the same 40 seeds. Detection is rescored at six windows, from 120 to 600 seconds. The 180-second counterexample was observed during review before this grid was specified. All window comparisons and corrected economic endpoints are therefore exploratory.')
    p('We report descriptive Wilson 95% intervals for binary counts and existing paired bootstrap intervals where available. These intervals describe variability under the sampled model, not real-world prevalence. Repeated scoring of the same traces is not additional statistical replication. No confirmatory hypothesis tests or power claims are made.')

    page();p('5. Order splitting defeats per-order checks','h1')
    p('In the initial evaluation, both programmed positive controls trigger conduct alerts in all 40 ungated seeds per market response and none under the legacy gate. The four benign strategies produce no conduct alerts. For the responsive spoofer, mean wealth changes from 24.25 to -23.75 while executed volume stays at 24 lots. In the unresponsive market the ungated spoofer still alerts but earns -23.75. The alert identifies the sequence even when the stipulated payoff is absent.')
    rows=[]
    labels={'scripted_spoofer':'One 60-lot order','split_3x20':'Three 20-lot orders','split_6x10':'Six 10-lot orders'}
    for strategy in labels:
        for gate in ('off','legacy','aggregate'):
            r=cell('review-v0.4',strategy=strategy,book_aware=True,gate=gate)
            rows.append([labels[strategy],gate,f"{r['legacy_conduct']['k']}/40",f"{r['extended_conduct']['k']}/40",n(r['filled_lots']),n(r['wealth_fundamental'])])
    table(['Displayed sequence','Gate','Original alerts','Extended alerts','Lots','Wealth'],rows,[.28,.15,.14,.14,.11,.18])
    p('Table 2. Responsive-market repair cohort, seeds 3000-3039. Alerts are episode counts; volume and wealth are means. Wealth is in tick-lots. Extended alerts include the original detectors and the aggregate pattern. The table uses the historical 120-second window.','caption')
    p('The six-by-ten sequence is accepted by the legacy size check and occupies only one price level. No individual order meets the three-times-depth detection threshold, and one level cannot satisfy the layer detector. It therefore passes both checks in all 40 seeds. Aggregation closes this particular route: the detector flags the ungated and legacy-gated sequence, and the gate removes its response benefit while retaining the small execution leg.')
    p('The three-by-twenty sequence does not pass the legacy gate consistently because noise can reduce reference depth below ten lots. Its lower mean wealth under that gate is a useful distinction: splitting size alone does not guarantee admission. The aggregate check is still related to the aggregate detector through displayed size. Their agreement is not evidence from independent detection families.')
    p('The six-by-ten sequence has mean price error 0.1331 ticks versus 0.0666 for the one-by-sixty sequence. Both receive the same two-tick response and have identical wealth within each ungated seed. The split sequence holds that displacement longer because its submissions consume more simulated seconds. The price-error difference is duration, not a larger response. Likewise, 22.05 here and 24.25 in the initial cohort reflect different seed paths, not a change in economic mechanism.')

    page();p('6. Trading cost depends on throttle timing','h1')
    p('The repair cohort initially appeared to favor the aggregate gate on the large legitimate maker: mean volume was 51.40 lots, compared with 23.98 under the legacy gate. The throttle-isolation cohort explains this reversal. The legacy gate admits four new quotes per decision; the aggregate gate generally admits only one level per side. Accepted submissions therefore reach the throttle\'s minimum count earlier under the legacy gate.')
    figure('throttle.png',2.4,'Figure 1. Large-maker means in the throttle-isolation cohort, seeds 4000-4039. Separate panels preserve units. The ungated maker executes 59.48 lots; disabling only the aggregate gate\'s throttle restores volume from 50.93 to 59.05 lots.')
    p('On seed 4000 the legacy arm begins rejecting new submissions at about 304 seconds; the aggregate arm reaches the same condition at about 602 seconds. Because the 1,800-second window does not expire during the 720-second session, a triggered lockout persists. Removing the throttle from the aggregate arm recovers 8.13 lots on average (paired bootstrap 95% interval 6.48-9.70). Its remaining mean difference from the ungated arm is 0.425 lots. That difference describes this maker, these paths, and this engine; it is not a universal cost of aggregate limits.')
    rows=[]
    for strategy,label in [('vwap_executor','Passive scheduled executor'),('iceberg','10-lot slice executor'),('market_maker','Small maker'),('large_market_maker','Large maker'),('patient_large_maker','Patient maker, 300 s')]:
        a=cell('negative-controls-v0.4',strategy=strategy,gate='off');b=cell('negative-controls-v0.4',strategy=strategy,gate='aggregate')
        rows.append([label,n(a['filled_lots']),n(b['filled_lots']),n(b['filled_lots']-a['filled_lots']),n(b['rejected_actions'])])
    table(['Legitimate task','Lots, off','Lots, gate','Difference','Rejections'],rows,[.40,.15,.15,.15,.15])
    p('Table 3. Negative controls, seeds 6000-6039; means over 40 episodes per arm. The scheduled executor is named vwap_executor in code but uses no volume forecast or VWAP benchmark. It and the slice executor fill only 34.2% and 37.7% of their 80-lot parents in the short session. Zero gate rejections do not establish adequate execution quality.','caption')

    page();p('7. Economic attribution in parameterized probes','h1')
    p('At the historical 120-second window, 15 of the 40 parameter draws both move the midpoint away from the fundamental and earn more wealth in the responsive market than in the ungated unresponsive reference. The original and aggregate detector union identifies 10 of these 15. Five slow draws evade both pattern families: gaps and execution delays make too little qualifying displayed quantity remain inside the cancellation window. Increasing the shared window to 180 seconds identifies all 15 on the same draws. This is a tuning result, not an independent recall estimate.')
    ev=[summary['historical'][name] for name in ('evasion-family-v0.4','evasion-family-v0.5-window180')]
    counts=lambda key:[f"{s['counts'][key]}/{s['draws']}" for s in ev]
    recalls=[r for r in summary['historical_window_sweep']['table'] if r['variant']=='hard' and r['horizon_s'] in (120,180)]
    table(['Readout','120-second window','180-second window'],[
        ['Original detector family, gate off',*counts('legacy_conduct_off')],
        ['Aggregate pattern, gate off',*counts('aggregate_pattern_off')],
        ['Union on the selected economic cases',*[f"{r['recall_attributable']}/{r['n_attributable']}" for r in recalls]],
        ['At least one gate rejection',*counts('gate_blocks')],
        ['Conduct alert under aggregate gate',*[f"{sum(r['legacy_conduct_gate'] or r['aggregate_pattern_gate'] for r in s['per_draw'])}/{s['draws']}" for s in ev]]
    ],[.54,.23,.23])
    p('Table 4. Identical parameter draws and market paths rescored under two shared windows. Original-family counts include self-match alerts; they are not all spoof detections. A rejected order is an operational event, not proof that the complete sequence was prevented.','caption')
    p('The historical residual-route rule combines positive absolute wealth, no gate rejection, and no ungated conduct alert. It marks one draw: a disjoint sequence that earns 3.70 in both responsive and unresponsive markets with zero price error. The profit is unrelated to the engineered response. Other gated draws also retain positive wealth, and some have small positive price error. Neither readout alone identifies an economic residual.')
    p('We therefore add the missing gated unresponsive condition for each draw. Both sides of the comparison use the same gate rules, so differences caused solely by enabling a gate are not attributed to the market-response rule. Fills, rejections, and fees remain outcomes of each run. In these 40 matched pairs the full event streams are identical and the wealth differences are exactly zero at the engine\'s recorded precision. No tested draw retains a response-dependent wealth advantage under the aggregate gate. This result is established by the matched records, not by the absence of alerts.')
    p('For a zero count in 40 draws, the descriptive Wilson 95% upper limit is 8.76%. This is conditional on the authored generator, its restricted parameter space, and the one-seed-per-draw assignment. It does not bound all manipulation strategies. The response comparison can also miss harmful outcomes unrelated to the particular imbalance rule. We retain the historical residual fields unchanged for provenance and publish the corrected endpoints separately.')

    page();p('8. Legitimate cadence breaks the selected window','h1')
    p('The earlier window sweep selected 180 seconds because it recovered the 15 economic cases without flagging any of five legitimate controls. Two of those controls buy only; they cannot satisfy an opposite-side-own-fill condition. The small maker remains below the size threshold, and the patient maker re-quotes at 300 seconds. These controls leave a gap around the selected 180-second boundary.')
    figure('cadence.png',2.7,'Figure 2. New cadence stress test: conduct alerts among 40 ungated legitimate-maker episodes per cell. Orders are generated once for each cadence and seed, then rescored at each shared window. Cells are paired and are not independent samples. The strategy quotes on both sides without a spoofing objective.')
    r180=next(c for c in summary['cadence_cells'] if c['cadence']==180 and c['gate']=='off')
    k=r180['window_alerts']['180'];ci=r180['window_wilson95']['180']
    p(f'At the chosen 180-second detection window, the 180-second maker cadence triggers alerts in {k}/40 episodes ({100*k/40:.1f}%; descriptive Wilson 95% interval {100*ci[0]:.1f}-{100*ci[1]:.1f}%). The 300-second cadence triggers none at that window. The quote quantities, market response, and seed cohort are the same. Legitimate quote cancellation after an opposite-side fill is sufficient to satisfy the aggregate pattern when enough displayed quantity remains unfilled.')
    p('The detector does not observe the script\'s objective; that label is available because this is a controlled experiment. In deployment an alert would require review. Here the result is a false alert relative to the declared legitimate control, and it demonstrates why zero alerts on one cadence should not be described as a low general false-positive rate. Replaying the same 15 tuned evasion cases does not repair this gap.')
    p('The historical sweep also evaluated a linearly age-weighted aggregate signal. It reached 14/15 of the selected economic cases at a 600-second horizon, compared with 15/15 for the hard 180-second window, with no lower observed false-alert count in the original controls. We do not choose a replacement window from the expanded grid. A future choice needs separate development and test strategy families, including multiple legitimate quoting cadences and adequate execution tasks.')
    gated180=next(c for c in summary['cadence_cells'] if c['cadence']==180 and c['gate']=='aggregate')
    p(f"Under the aggregate gate, the 180-second maker produces no conduct alerts at any tested window, but mean volume falls from {r180['filled_lots']:.2f} to {gated180['filled_lots']:.2f} lots. The accepted display is smaller, so the detector no longer encounters the same pattern. This reduction in alerts must be read alongside the loss of legitimate execution, rather than treated as independent evidence of better discrimination.")

    page();p('9. Interpretation and limitations','h1')
    p('The six-by-ten counterexample changes how displayed size is divided among orders while preserving its effect on the response mechanism. Both the per-order gate and individual-order detector miss that representation change. Aggregating outstanding quantity repairs the tested case, but the detector and gate still rely on closely related size rules. Their agreement is therefore limited evidence of broader protection.')
    p('A size cap also changes how quickly accepted submissions accumulate. That changes when the ratio throttle becomes active and can reverse the apparent ranking of complete policies. The 12-minute horizon magnifies the interaction because the cancellation window never expires. Longer sessions could produce repeated lockout and recovery; those dynamics remain unmeasured.')
    p('The legitimate controls determine which detector weaknesses an evaluation can reveal. One-sided executors cannot challenge a rule requiring opposite-side fills. A maker that re-quotes after the cancellation window produces no alerts, while the same quoting rule at a shorter cadence is frequently flagged. The 180-second counterexample exposes a gap in the original calibration set.')
    p('The engine has one evaluated account, a small replenishing book, fixed spread around a synthetic fundamental, and a stipulated two-tick response. It has no exchange latency, queue calibration, inventory-managed background population, closing auction, beneficial-owner graph, or endogenous strategic learning. The fundamental is a discrete random walk rather than the mean-reverting process described in the earlier LLM proposal. These choices permit controlled replay but sharply limit external validity.')
    p('The experiments are sequentially developed. The split probe motivated aggregation; the slow draws motivated a longer window; the cadence counterexample motivated the new grid. No study in this paper is externally preregistered. The parameter sample is not a held-out strategy family, and its reuse in detector selection precludes an independent estimate of selected-window recall. Reported intervals do not account for that selection. Additional unseen strategy families and independently reviewed labels are needed.')
    p('No LLM inference was used to choose trading actions in these experiments. The repository includes a provider interface and mock tests, but these supply no evidence about model behaviour, instruction following, collusion, or internal intent. A future model study must choose and freeze its environment, model versions, prompts, call budget, failure handling, endpoints, and analysis before collecting headline results. Missing explanations must remain distinct from explicit benign explanations. The present paper makes no annotation-agreement or power claim.')
    p('Regulatory obligations do not disappear when a trading workflow uses generative AI. FINRA explicitly states that its rules are technologically neutral [6]. Our simulated gates are engineering controls, not an implementation or certification of all applicable obligations. No orders were sent to a live market, no customers were studied, and no finding about commercial demand is drawn from these experiments.')

    page();p('10. Reproducibility and conclusion','h1')
    p('10.1. Artifact and verification','h2')
    p('The new analysis preserves all historical code and evidence. It adds matching gated counterfactuals, a cadence grid, corrected per-side display measurement, and an executable economic endpoint. Every new record stores the attempted-action sequence, subject-related events, rejection reasons, detector outputs, and a hash of the full event stream. Background events are reconstructed deterministically rather than claimed to be fully present in the compressed subject-event archive.')
    p('Verification replays attempted actions without invoking the strategy generator, checks the full-event hash and recorded fields, recomputes every new CSV row and summary, and checks the historical source and evidence hashes. Exact numeric reproduction uses CPython 3.9.6, matching the saved cohorts. Different Python summation implementations can change the last bits of bootstrap summaries. No provider credential or network access is needed to reproduce the measurements.')
    p('Run from the repository root using the pinned interpreter:','small')
    for line in ['python3 -m unittest discover -s tests -v',
                 'python3 -m paper_study.evaluate verify results/paper-review-v1',
                 'python3 -m paper_study.evaluate run results/local-paper-review',
                 'python3 -m paper_study.evaluate verify results/local-paper-review']:
        p(escape(line),'code')
    p('Use a fresh output directory for regeneration. The study reads frozen historical configurations, including the 180-second evasion configuration, rather than the mutable root config. The older pilot regeneration command reads the root config and therefore should not be used to claim byte-identical reproduction of a 120-second cohort after the default changes. Historical replay commands use each cohort\'s saved configuration.')
    p('The manuscript tables and figures are generated from the new summary, which includes checked historical summaries. The builder writes an editable structured manuscript, reference list, figure files, and a document manifest linking this PDF to its source and evidence hashes. The repository is access-controlled at the time of this draft; this is an artifact availability statement, not a claim of public release.')
    p('Source snapshot: <link href="https://github.com/tokencraftdata/agentic-compliance/tree/'+commit+'">tokencraftdata/agentic-compliance, '+commit+'</link>','small')
    p('10.2. Conclusion','h2')
    p('Order splitting, cancellation timing, and throttle accounting expose different weaknesses in the same control stack. Aggregation prevents the tested response-dependent payoff, but a detector window selected on a narrow control set produces frequent alerts when a legitimate maker changes cadence. The evidence supports explicit separation of alerts, economic effects, and execution cost. It also supplies a testbed in which a future agent study can fail these checks before its results are interpreted.')
    p('10.3. Use of generative AI tools','h2')
    p('OpenAI Codex assisted with implementation, tests, analysis scripts, literature checks, and manuscript drafting and revision. The reported trading experiments used scripted policies and made no LLM calls. AI assistance in producing the research artifact is separate from the policies evaluated by the simulator. The human author is responsible for the methods, results, and references reported in the paper.')

    page();p('References','h1')
    for i,(text,url) in enumerate(REFS,1):
        p(f'[{i}] {escape(text)}<br/><link href="{url}" color="#254f70">{escape(url)}</link>','small')
    p('References checked against primary sources on 9 November 2026. Bibliographic scope is limited to the implemented offline study.','small')
    p('Artifact map','h1')
    table(['Path','Contents'],[
        ['paper_study/evaluate.py','Corrected endpoints, new grid, action replay, CSV consistency checks'],
        ['results/paper-review-v1/','360 new episodes, plan, configuration, summary, and hash manifest'],
        ['paper/offline_study/build.py','Paper and figure builder'],
        ['paper/offline_study/manuscript.json','Editable structured manuscript exported by the builder'],
        ['paper/offline_study/figures/','Standalone PNG and SVG figures'],
        ['paper/offline_study/REVIEW_RESPONSE.md','Disposition of the manuscript and code review findings']
    ],[.50,.50])
    p('Version note','h1')
    p('Version 1.2 retains the clarified order-splitting and matched-comparison claims, embeds outline fonts, and discloses generative AI assistance in producing the artifact. It includes the author details for the planned arXiv submission. The experimental results are unchanged. Historical reports and their original measurements remain in the artifact. The experiments are exploratory and were not externally preregistered.')

    def footer(canvas, doc):
        canvas.saveState();canvas.setFont('StudySerif',8);canvas.setFillColor(colors.HexColor('#555555'))
        canvas.drawString(.875*inch,.45*inch,'Order Splitting and Timing | Offline study v1.2')
        canvas.drawRightString(7.625*inch,.45*inch,str(doc.page));canvas.restoreState()
    doc=SimpleDocTemplate(str(out),pagesize=(8.5*inch,11*inch),rightMargin=.875*inch,leftMargin=.875*inch,
                          topMargin=.65*inch,bottomMargin=.7*inch,title=TITLE,author='Sandip Nandi')
    doc.build(story,onFirstPage=footer,onLaterPages=footer,
              canvasmaker=partial(Canvas, initialFontName='StudySerif'))
    (HERE/'manuscript.json').write_text(json.dumps({'title':TITLE,'subtitle':SUBTITLE,
        'authors':[{'name':'Sandip Nandi','affiliation':'Independent Researcher'}],
        'blocks':source},indent=2)+'\n')
    (HERE/'references.json').write_text(json.dumps([{'number':i,'text':t,'url':u} for i,(t,u) in enumerate(REFS,1)],indent=2)+'\n')
    manifest={'document_version':'1.2','date':'2026-11-09','code_commit':commit,'python':platform.python_version(),
              'reportlab':reportlab.Version,'matplotlib':matplotlib.__version__,
              'source_sha256':digest(Path(__file__)),'evidence_manifest_sha256':digest(DATA/'manifest.json'),
              'summary_sha256':digest(DATA/'summary.json'),'pdf_sha256':digest(out),
              'pdf_bytes':out.stat().st_size,'manuscript_sha256':digest(HERE/'manuscript.json')}
    (HERE/'document_manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print(json.dumps({'pdf':str(out),'bytes':out.stat().st_size},indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--code-commit',required=True)
    args=parser.parse_args()
    if len(args.code_commit)!=40 or any(c not in '0123456789abcdef' for c in args.code_commit):
        parser.error('Use a full immutable 40-character source commit SHA')
    build(args.code_commit)
