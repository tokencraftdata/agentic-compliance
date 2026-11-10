"""Build the offline paper and standalone figures from saved measurements.

Usage: python paper/offline_study/build.py --code-commit <immutable SHA>
Dependencies: requirements.txt in this directory. Numeric reproduction is stdlib.
"""
import argparse
import hashlib
import json
import platform
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
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image, KeepTogether

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
DATA = ROOT / 'results/paper-review-v1'
TITLE = 'Evaluating Controls for Trading Compliance: An Offline Study of Order Splitting and Cancellation Timing'
SUBTITLE = 'Scripted experiments on surveillance and pre-trade gates'
REFS = [
    ('Xintong Wang and Michael P. Wellman. Spoofing the Limit Order Book: An Agent-Based Model. AAMAS, 2017, pp. 651-659.', 'https://www.ifaamas.org/Proceedings/aamas2017/pdfs/p651.pdf'),
    ('David Byrd. Learning Not to Spoof. Proceedings of the Third ACM International Conference on AI in Finance (ICAIF), 2022, pp. 139-147.', 'https://doi.org/10.1145/3533271.3561767'),
    ('Xintong Wang and Michael P. Wellman. Market Manipulation: An Adversarial Learning Framework for Detection and Evasion. IJCAI, 2020, pp. 4626-4632.', 'https://doi.org/10.24963/ijcai.2020/638'),
    ('David Byrd, Maria Hybinette, and Tucker Hybinette Balch. ABIDES: Towards High-Fidelity Multi-Agent Market Simulation. Proceedings of the 2020 ACM SIGSIM Conference on Principles of Advanced Discrete Simulation, 2020, pp. 11-22.', 'https://doi.org/10.1145/3384441.3395986'),
    ('Eren Kurshan, Tucker Balch, and David Byrd. The Agentic Regulator: Risks for AI in Finance and a Proposed Agent-based Framework for Governance. arXiv:2512.11933, 2025.', 'https://arxiv.org/abs/2512.11933'),
    ('FINRA. Regulatory Notice 24-09: Artificial Intelligence and Large Language Models. June 27, 2024.', 'https://www.finra.org/rules-guidance/notices/24-09'),
    ('Xintong Wang, Christopher Hoang, Yevgeniy Vorobeychik, and Michael P. Wellman. Spoofing the Limit Order Book: A Strategic Agent-Based Analysis. Games 12(2), 46, 2021.', 'https://doi.org/10.3390/g12020046'),
    ('Yiyang Luo et al. ReguSim: Evaluating LLM Agent Rule Grounding in Financial Compliance. arXiv:2608.19974, version 2, 2026.', 'https://arxiv.org/abs/2608.19974v2'),
    ('Ailiya Borjigin and Cong He. Safe and Compliant Cross-Market Trade Execution via Constrained RL and Zero-Knowledge Audits. arXiv:2510.04952, version 2, 2025.', 'https://arxiv.org/abs/2510.04952v2'),
    ('Timothée Fabre and Damien Challet. Learning the Spoofability of Limit Order Books With Interpretable Probabilistic Neural Networks. arXiv:2504.15908, 2025.', 'https://arxiv.org/abs/2504.15908'),
    ('David Byrd. The Accidental Pump and Dump: When Agentic AI Meets Autonomous Trading. ICAIF, 2025, pp. 88-95.', 'https://doi.org/10.1145/3768292.3770424'),
    ('Álvaro Cartea, Patrick Chang, and Gabriel García-Arenas. Spoofing and Manipulating Order Books with Learning Algorithms. SSRN 4639959, 2023; revised February 17, 2025.', 'https://doi.org/10.2139/ssrn.4639959'),
    ('Bao Linh Do and Tālis J. Putniņš. Detecting Layering and Spoofing in Markets. SSRN 4525036, 2023.', 'https://doi.org/10.2139/ssrn.4525036'),
    ('FINRA. Regulatory Notice 15-09: Guidance on Effective Supervision and Control Practices for Firms Engaging in Algorithmic Trading Strategies. March 26, 2015.', 'https://www.finra.org/rules-guidance/notices/15-09'),
    ('FINRA. Rule 3110: Supervision. Rulebook, accessed November 9, 2026.', 'https://www.finra.org/rules-guidance/rulebooks/finra-rules/3110'),
    ('FINRA. Rule 5210: Publication of Transactions and Quotations, including Supplementary Material .02 and .03. Rulebook, accessed November 9, 2026.', 'https://www.finra.org/rules-guidance/rulebooks/finra-rules/5210'),
    ('Anri Gu et al. The Effect of Liquidity on the Spoofability of Financial Markets. ICAIF, 2024, pp. 239-247.', 'https://doi.org/10.1145/3677052.3698634')
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
    # SimpleDocTemplate's text frame has six-point padding on each side.
    width = 6.5*inch - 12
    styles = {
        'body': ParagraphStyle('body', fontName='StudySerif', fontSize=11, leading=13.4,
                               firstLineIndent=14, spaceAfter=0, alignment=TA_JUSTIFY,
                               allowWidows=0, allowOrphans=0),
        'small': ParagraphStyle('small', fontName='StudySerif', fontSize=9.5, leading=11.5, spaceAfter=7),
        'abstract_heading': ParagraphStyle('abstract_heading', fontName='StudySerif-Bold', fontSize=10.5,
                                           leading=13, alignment=TA_CENTER, spaceBefore=12,
                                           spaceAfter=6, keepWithNext=True),
        'abstract': ParagraphStyle('abstract', fontName='StudySerif', fontSize=10, leading=12.2,
                                   leftIndent=24, rightIndent=24, alignment=TA_JUSTIFY,
                                   spaceAfter=8, allowWidows=0, allowOrphans=0),
        'caption': ParagraphStyle('caption', fontName='StudySerif', fontSize=9.5, leading=11.5,
                                  spaceBefore=5, spaceAfter=10, alignment=TA_JUSTIFY),
        'title': ParagraphStyle('title', fontName='StudySerif', fontSize=18, leading=22,
                                alignment=TA_CENTER, spaceAfter=10),
        'subtitle': ParagraphStyle('subtitle', fontName='StudySerif', fontSize=11, leading=14,
                                   alignment=TA_CENTER, spaceAfter=10),
        'h1': ParagraphStyle('h1', fontName='StudySerif-Bold', fontSize=13, leading=16,
                             spaceBefore=15, spaceAfter=8, keepWithNext=True),
        'h2': ParagraphStyle('h2', fontName='StudySerif-Bold', fontSize=11.5, leading=14,
                             spaceBefore=12, spaceAfter=6, keepWithNext=True),
        'cell': ParagraphStyle('cell', fontName='StudySerif', fontSize=9, leading=11),
        'code': ParagraphStyle('code', fontName='StudyMono', fontSize=8, leading=11, spaceAfter=6),
    }
    styles['body_first'] = ParagraphStyle('body_first', parent=styles['body'], firstLineIndent=0)
    styles['reference'] = ParagraphStyle('reference', parent=styles['small'],
                                        leading=11.2, spaceAfter=5)
    story, source = [Spacer(1, 24)], []
    def p(text, style='body'):
        previous = source[-1] if source else {}
        display_style = style
        if text == 'Abstract':
            display_style = 'abstract_heading'
        elif previous.get('text') == 'Abstract':
            display_style = 'abstract'
        elif style == 'body' and previous.get('type') in ('h1', 'h2'):
            display_style = 'body_first'
        paragraph = Paragraph(text, styles[display_style])
        if style == 'caption' and previous.get('type') == 'table':
            # A table and its explanation travel together in continuous flow.
            story[-1] = KeepTogether([story[-1], paragraph])
        else:
            story.append(paragraph)
        source.append({'type': style, 'text': text})
    def table(headers, rows, fractions):
        data = [[Paragraph('<b>'+escape(str(x))+'</b>', styles['cell']) for x in headers]]
        data += [[Paragraph(escape(str(x)), styles['cell']) for x in row] for row in rows]
        t = Table(data, colWidths=[width*x for x in fractions], repeatRows=1,
                  splitByRow=0, hAlign='LEFT')
        t.setStyle(TableStyle([('FONTNAME',(0,0),(-1,-1),'StudySerif'),
             ('VALIGN',(0,0),(-1,-1),'TOP'),('LINEABOVE',(0,0),(-1,0),0.7,colors.black),
             ('LINEBELOW',(0,0),(-1,0),0.4,colors.black),('LINEBELOW',(0,-1),(-1,-1),0.6,colors.black),
             ('LEFTPADDING',(0,0),(-1,-1),4),('RIGHTPADDING',(0,0),(-1,-1),4),
             ('TOPPADDING',(0,0),(-1,-1),4),('BOTTOMPADDING',(0,0),(-1,-1),4)]))
        t.spaceBefore = 8
        t.spaceAfter = 5
        story.append(t);source.append({'type':'table','headers':headers,'rows':rows})
    def figure(name, caption):
        image = Image(str(figs/name))
        # Read the lazy image dimensions before assigning the displayed size.
        image.drawHeight = width * image.imageHeight / image.imageWidth
        image.drawWidth = width
        image.spaceBefore = 8
        story.append(KeepTogether([image, Paragraph(caption, styles['caption'])]))
        source.append({'type':'caption', 'text':caption})
    def cell(name, **kw):
        return next(c for c in summary['historical'][name]['cells'] if all(c[k] == v for k,v in kw.items()))
    def n(x):return f'{x:.2f}'
    p(TITLE.replace(': ', ':<br/>', 1),'title');p(SUBTITLE,'subtitle')
    p('Sandip Nandi<br/>Independent Researcher','subtitle')
    p('9 November 2026','subtitle')
    p('Abstract','h1')
    p("An order-level size check can admit a large displayed quantity when it arrives as several small orders. We examine this failure and its repair in a replayable limit-order-book simulator. Six 10-lot orders pass the legacy pre-trade gate and its original detectors in all 40 tested seeds. A cap on total same-side quantity removes the benefit from the model's imbalance response for this probe. Its apparent advantage over the legacy gate on legitimate market making, however, largely reflects when a cancellation throttle activates. In a separate comparison of 40 parameterized probes, responsive and unresponsive markets produce identical event streams under the aggregate gate, with zero wealth differences. Detector tuning exposes another weakness: a 180-second window flags a legitimate maker in 34 of 40 episodes when it re-quotes every 180 seconds, compared with none at a 300-second cadence. The experiments show how order representation and timing can undermine the evaluation of trading-compliance controls. They use fixed scripts in a synthetic market; they neither establish legal compliance nor evaluate LLM trading.")

    p('1. Introduction','h1')
    p('The legacy gate rejects the 60-lot displayed order used in this study. Splitting it into six 10-lot orders makes the intended display admissible: each order falls below the size cap, and the sequence occupies only one price level. The script then trades on the opposite side and cancels the remaining display without triggering the original detectors. The control has checked each order, but missed the sequence that matters to the market response.')
    p("Order splitting is also part of ordinary execution. Our legitimate execution scripts divide an 80-lot purchase into smaller orders. The spoofing-like script uses displayed quantity to induce a price response before trading on the other side. Both submit and cancel orders, so a useful control must be tested against both. The script definitions supply the labels in this study; the order patterns alone do not establish a trader's intent.")
    p('We examine surveillance rules, which flag completed patterns, and pre-trade gates, which accept or reject proposed actions. Adding a cap on aggregate display repairs the split-order case, but also changes legitimate execution and the point at which a cancellation throttle activates. A longer surveillance window catches slow variants of the scripted sequence, then flags a legitimate maker that quotes at a different cadence. These interactions require comparisons of the recorded trades and wealth, as well as the alerts.')
    p("The simulator was built for the Agentic Compliance project's planned study of LLM trading agents. Testing it with fixed scripts exposed these failures before any model was run. The present paper reports that control evaluation. FINRA guidance on algorithm testing and supervision [14,15], together with Rule 5210's market-conduct provisions [16], supplies its regulatory context. The connection is to testing components of a supervisory system, not certifying a firm's compliance. Section 9.1 sets out the specific connections and gaps.")

    p('2. Setting and scope','h1')
    p('2.1. Relation to previous work','h2')
    p("Agent-based studies have examined how spoofing exploits traders' use of order-book information. Wang and Wellman [1] model that interaction; Wang, Hoang, Vorobeychik, and Wellman [7] extend and supersede the earlier study with strategic responses, book cloaking, and modified learning strategies. Byrd [2] shows a reinforcement-learning trader discovering spoofing and uses a learned recognizer to discourage it. Cartea, Chang, and García-Arenas [12] derive conditions under which learning algorithms manipulate an order book. Here the trading programs are fixed, allowing us to examine control failures without a learning policy changing at the same time.")
    p('The detection literature uses several kinds of evidence. Wang and Wellman [3] study adversarial learning between manipulation and detection. Do and Putniņš [13] use prosecuted cases and out-of-sample evaluation, while Fabre and Challet [10] use Level-3 market data to estimate price responses and potential spoofing gains. Our synthetic response and script-based labels serve a narrower purpose: controlled comparisons of changes to the gate and detector. They cannot yield real-market accuracy or profitability estimates.')
    p("ReguSim [8] is a close comparison for agent compliance. It records an LLM trader's stated reasoning and attempted orders separately from enforcement and monitoring. Its trader experiment uses exogenous prices and full execution of accepted orders, without order-book interaction or market impact. Our simulator adds partial fills and cancellations to a small order book, so the tests can follow a displayed order through opposite-side execution and withdrawal. The experiments in this paper do not compare LLMs.")
    p("Other work addresses execution constraints and governance. Borjigin and He [9] propose constrained reinforcement learning with a runtime action shield and cryptographic auditing; the full proof pipeline remains future work. Kurshan, Balch, and Byrd [5] propose an agent-based governance architecture and discuss a spoofing case. Byrd's pump-and-dump study [11] combines an RL trader with LLM-generated social posts, an information channel absent from our environment.")
    p('Market design also limits what a simulation can establish. ABIDES [4] supports interacting agents and communication latencies. Gu et al. [17] vary market-maker liquidity and compare baseline, tuned, and reinforcement-learned spoofing strategies. Our engine is purpose-built, with fixed background liquidity and no calibration to ABIDES or exchange data. It isolates order-level control failures but leaves their sensitivity to richer market conditions unresolved.')

    p('2.2. Market mechanics','h2')
    p('All orders execute inside the simulator; no broker or exchange is connected. This is the sense of offline used here. The trading policies are separately specified as scripts: no model is trained or queried to select their actions. A future LLM study could also run offline in this environment.')
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

    p('3. Controls, detectors, and outcomes','h1')
    p('3.1. Gates','h2')
    p('Every arm enforces the 100-lot position-commitment bound. The legacy gate also caps each limit submission at twice the current external reference depth, permits at most two resting price levels per side, rejects a predicted self-match using the matching engine\'s price/time walk, and throttles new submissions when cancellations divided by accepted submissions exceeds 0.9 after at least 20 accepted submissions in the window. External reference depth is the median of the two external touch quantities, excluding subject orders.')
    p('The aggregate gate retains those checks and caps the sum of remaining same-side subject limit quantity plus the full new quantity at twice reference depth. This is conservative: even a marketable limit is checked at its full submitted size. The check occurs at submission; it does not continuously rebalance orders when external depth changes. The throttle-off arm disables only the aggregate gate\'s submission throttle. The throttle window is 1,800 seconds, longer than every session studied here.')
    p('3.2. Pattern detectors','h2')
    p('The individual spoof pattern requires a nonmarketable order of at least three times submission-time reference depth, within three ticks of touch, less than 10% filled, an intervening opposite-side subject fill, and cancellation within the detection window. The layer pattern requires qualifying cancelled orders at at least three distinct levels sharing an opposite-side fill. Each of the three levels must enter the cancelled-order candidate set.')
    p('The aggregate pattern combines at least two qualifying orders live at a shared opposite-side fill, using their remaining quantities at that fill and the maximum of their submission-time reference depths. Self-matches are reported separately. A cancellation-ratio diagnostic is excluded from the conduct-alert union; its historical threshold of 1.0 is uninformative at this session length. No close-marking or cross-account wash-trading detector is implemented. The spoof, layer, and aggregate patterns share the same window parameter: changing it from 120 to 180 seconds changes all three.')
    p('3.3. Economic and operational outcomes','h2')
    p('Terminal wealth is cash plus inventory valued at the terminal fundamental, after fees. We also report filled lots, rejection reasons, inactivity, and mean absolute midpoint-minus-fundamental error. Price error is a diagnostic, not an abuse or welfare label: legitimate makers can generate it. Economic comparisons use the same seed. We do not interpret wealth differences between seed cohorts as strategy effects.')
    p('<b>Response-dependent wealth advantage:</b> D(g,d) = W(responsive, g, d) - W(unresponsive, g, d), where g is the gate and d fixes both the parameter draw and market seed. A positive D under the aggregate gate with no conduct alert is reported even if some orders were rejected. This replaces the historical summary\'s absolute-wealth and zero-rejection definition. The comparison isolates the response toggle within this model; it is not a legal definition of manipulation.')

    p('4. Experimental design and provenance','h1')
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

    p('5. Order splitting defeats per-order checks','h1')
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

    p('6. Trading cost depends on throttle timing','h1')
    p('The repair cohort initially appeared to favor the aggregate gate on the large legitimate maker: mean volume was 51.40 lots, compared with 23.98 under the legacy gate. The throttle-isolation cohort explains this reversal. The legacy gate admits four new quotes per decision; the aggregate gate generally admits only one level per side. Accepted submissions therefore reach the throttle\'s minimum count earlier under the legacy gate.')
    figure('throttle.png','Figure 1. Large-maker means in the throttle-isolation cohort, seeds 4000-4039. Separate panels preserve units. The ungated maker executes 59.48 lots; disabling only the aggregate gate\'s throttle restores volume from 50.93 to 59.05 lots.')
    p('On seed 4000 the legacy arm begins rejecting new submissions at about 304 seconds; the aggregate arm reaches the same condition at about 602 seconds. Because the 1,800-second window does not expire during the 720-second session, a triggered lockout persists. Removing the throttle from the aggregate arm recovers 8.13 lots on average (paired bootstrap 95% interval 6.48-9.70). Its remaining mean difference from the ungated arm is 0.425 lots. That difference describes this maker, these paths, and this engine; it is not a universal cost of aggregate limits.')
    rows=[]
    for strategy,label in [('vwap_executor','Passive scheduled executor'),('iceberg','10-lot slice executor'),('market_maker','Small maker'),('large_market_maker','Large maker'),('patient_large_maker','Patient maker, 300 s')]:
        a=cell('negative-controls-v0.4',strategy=strategy,gate='off');b=cell('negative-controls-v0.4',strategy=strategy,gate='aggregate')
        rows.append([label,n(a['filled_lots']),n(b['filled_lots']),n(b['filled_lots']-a['filled_lots']),n(b['rejected_actions'])])
    table(['Legitimate task','Lots, off','Lots, gate','Difference','Rejections'],rows,[.40,.15,.15,.15,.15])
    p('Table 3. Negative controls, seeds 6000-6039; means over 40 episodes per arm. The scheduled executor is named vwap_executor in code but uses no volume forecast or VWAP benchmark. It and the slice executor fill only 34.2% and 37.7% of their 80-lot parents in the short session. Zero gate rejections do not establish adequate execution quality.','caption')

    p('7. Economic attribution in parameterized probes','h1')
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

    p('8. Legitimate cadence breaks the selected window','h1')
    p('The earlier window sweep selected 180 seconds because it recovered the 15 economic cases without flagging any of five legitimate controls. Two of those controls buy only; they cannot satisfy an opposite-side-own-fill condition. The small maker remains below the size threshold, and the patient maker re-quotes at 300 seconds. These controls leave a gap around the selected 180-second boundary.')
    figure('cadence.png','Figure 2. New cadence stress test: conduct alerts among 40 ungated legitimate-maker episodes per cell. Orders are generated once for each cadence and seed, then rescored at each shared window. Cells are paired and are not independent samples. The strategy quotes on both sides without a spoofing objective.')
    r180=next(c for c in summary['cadence_cells'] if c['cadence']==180 and c['gate']=='off')
    k=r180['window_alerts']['180'];ci=r180['window_wilson95']['180']
    p(f'At the chosen 180-second detection window, the 180-second maker cadence triggers alerts in {k}/40 episodes ({100*k/40:.1f}%; descriptive Wilson 95% interval {100*ci[0]:.1f}-{100*ci[1]:.1f}%). The 300-second cadence triggers none at that window. The quote quantities, market response, and seed cohort are the same. Legitimate quote cancellation after an opposite-side fill is sufficient to satisfy the aggregate pattern when enough displayed quantity remains unfilled.')
    p('The detector does not observe the script\'s objective; that label is available because this is a controlled experiment. In deployment an alert would require review. Here the result is a false alert relative to the declared legitimate control, and it demonstrates why zero alerts on one cadence should not be described as a low general false-positive rate. Replaying the same 15 tuned evasion cases does not repair this gap.')
    p('The historical sweep also evaluated a linearly age-weighted aggregate signal. It reached 14/15 of the selected economic cases at a 600-second horizon, compared with 15/15 for the hard 180-second window, with no lower observed false-alert count in the original controls. We do not choose a replacement window from the expanded grid. A future choice needs separate development and test strategy families, including multiple legitimate quoting cadences and adequate execution tasks.')
    gated180=next(c for c in summary['cadence_cells'] if c['cadence']==180 and c['gate']=='aggregate')
    p(f"Under the aggregate gate, the 180-second maker produces no conduct alerts at any tested window, but mean volume falls from {r180['filled_lots']:.2f} to {gated180['filled_lots']:.2f} lots. The accepted display is smaller, so the detector no longer encounters the same pattern. This reduction in alerts must be read alongside the loss of legitimate execution, rather than treated as independent evidence of better discrimination.")

    p('9. Implications for compliance control evaluation','h1')
    p('The split-order failure comes from a mismatch in what the rules observe. The legacy gate limits each submission, while the simulated price response depends on displayed quantity across the book. Several small orders can therefore induce the response that one rejected order would have induced. Aggregation aligns the gate with this particular mechanism. Because the revised gate and detector both depend on displayed size, agreement between them is weak evidence against other forms of manipulation.')
    p('The throttle comparison shows why a complete policy cannot be assessed by inspecting its size cap alone. Changing how many quotes are admitted changes how quickly the cancellation ratio reaches its activation threshold. A stricter display cap can consequently leave a legitimate maker trading for longer. In a 12-minute session, that ordering dominates the cost comparison because the throttle window never expires. Sessions long enough to include expiry and recovery are needed before drawing a broader conclusion about execution cost.')
    p("The cadence result exposes a gap in the negative controls. A buy-only executor cannot trigger a pattern requiring opposite-side fills, and a maker quoting below the size threshold cannot challenge the aggregate rule. Even the larger patient maker misses the critical timing boundary at its original cadence. Changing that cadence produces false alerts without changing the script's legitimate objective. Choosing another window on the expanded grid would repeat the same selection problem; evaluation on separate strategy families is the next test.")
    p('Recorded action sequences made these comparisons possible. They let us identify the first throttle rejection, rescore the same orders, and compare responsive and unresponsive event streams under identical gate rules. Such records could help an analyst investigate a control change. The study has not measured that review process, analyst workload, regulatory reporting, retention compliance, or tamper resistance.')

    p('9.1. Connection to FINRA supervision and market conduct','h2')
    p("The regulatory connection is to market-conduct supervision of algorithmic trading by FINRA member firms. Rule 3110 [15] requires a supervisory system and written procedures appropriate to the firm's activities; it also leaves supervisory responsibility with the member. This simulator tests candidate technical controls within that wider process. It does not assess staffing, supervisory procedures, or a firm's overall compliance.")
    p("The suggested practices in Notice 15-09 [14] address development and change management, testing before deployment, ongoing monitoring, and records of tests and defect remediation. It also recommends testing independently of code development. Our replay records permit inspection of a control change, but this study is developer-authored and supplies no independent validation or assessment of a firm's governance. The notice expressly cautions that applying its suggested practices alone need not satisfy all applicable obligations.")
    p("Rule 5210.03 [16] addresses frequent patterns of disruptive quoting and trading. Its Type 1 pattern includes display at multiple price levels, a change in supply and demand, opposite-side execution, and cancellation of the display. Our layering script resembles parts of that sequence; the same-price split probe does not reproduce its multiple-price-level condition. The detectors do not implement the rule's complete pattern requirements or cross-venue coverage. Nor does the same-account self-match check establish the beneficial-ownership and related-algorithm review contemplated by Rule 5210.02.")
    p('Our size caps, cancellation ratio, and 120- or 180-second windows are experimental choices, not FINRA-prescribed thresholds. An alert is evidence for investigation, not a legal finding, and a rejected action is not proof of overall compliance. Notice 24-09 [6] confirms that existing obligations continue to apply when member firms use generative AI; it creates no new requirements. It motivates the planned agent study without turning these scripted results into evidence about LLM oversight.')

    p('9.2. Limitations and the next agent study','h2')
    p('The engine has one evaluated account, a small replenishing book, fixed spread around a synthetic fundamental, and a stipulated two-tick response. It has no exchange latency, queue calibration, inventory-managed background population, closing auction, beneficial-owner graph, or endogenous strategic learning. The fundamental is a discrete random walk rather than the mean-reverting process described in the earlier LLM proposal. These choices permit controlled replay but sharply limit external validity.')
    p('The experiments are sequentially developed. The split probe motivated aggregation; the slow draws motivated a longer window; the cadence counterexample motivated the new grid. No study in this paper is externally preregistered. The parameter sample is not a held-out strategy family, and its reuse in detector selection precludes an independent estimate of selected-window recall. Reported intervals do not account for that selection. Additional unseen strategy families and independently reviewed labels are needed.')
    p('No LLM inference was used to choose trading actions in these experiments. The repository includes a provider interface and mock tests, but these supply no evidence about model behaviour, instruction following, collusion, or internal intent. A future model study must choose and freeze its environment, model versions, prompts, call budget, failure handling, endpoints, and analysis before collecting headline results. Missing explanations must remain distinct from explicit benign explanations. The present paper makes no annotation-agreement or power claim.')
    p('A follow-up study should test whether controls remain effective as an agent varies order representation and timing, while measuring legitimate task completion. It needs separate development and test strategy families, fixed policy thresholds, and evidence-matched comparisons of instructions, external enforcement, and any review agent. Those comparisons are proposed work. No orders were sent to a live market, no customers were studied, and no finding about commercial demand is drawn from these experiments.')

    p('10. Reproducibility and conclusion','h1')
    p('10.1. Artifact and verification','h2')
    p('The repository contains the simulator, frozen configurations, saved traces, and commands for replay and regeneration. Verification replays recorded actions without calling the strategy, checks full-event hashes, and recomputes the reported rows and summaries. Exact numerical reproduction uses CPython 3.9.6. Tables and figures are generated from the checked summary; a manifest records hashes of the source, evidence, manuscript, and PDF. Detailed setup instructions and the limitations of historical regeneration are in the repository README. The repository is currently access-controlled.')

    p('Source snapshot: <link href="https://github.com/tokencraftdata/agentic-compliance/tree/'+commit+'">tokencraftdata/agentic-compliance, '+commit+'</link>','small')

    p('10.2. Conclusion','h2')
    p('The aggregate gate fixes the split-order probe in this simulator: the tested scripts lose the wealth advantage caused by the engineered price response. That repair does not settle the evaluation. Throttle timing changes the apparent cost of the gate, and a legitimate quoting cadence defeats the selected detector window. The resulting traces provide concrete regression cases for further control development. Before introducing LLM traders, the next study should test these controls on separate strategy families and market conditions, with legitimate execution measured alongside suspicious activity.')

    p('10.3. Use of generative AI tools','h2')
    p('Two generative AI coding assistants were used to produce the research artifact: Anthropic Claude Code wrote the throttle-isolation, parameterized-evasion, negative-control, and detector-window-sweep modules, their tests, and the accompanying results notes; OpenAI Codex wrote the matched-counterfactual and cadence-grid study, the paper builder, and assisted with literature checks and manuscript drafting and revision. The reported trading experiments used scripted policies and made no LLM calls. AI assistance in producing the research artifact is separate from the policies evaluated by the simulator. The human author is responsible for the methods, results, and references reported in the paper.')

    p('References','h1')
    for i,(text,url) in enumerate(REFS,1):
        reference = f'[{i}] {escape(text)}<br/><link href="{url}" color="#254f70">{escape(url)}</link>'
        paragraph = Paragraph(reference, styles['reference'])
        if i == 1:
            story[-1] = KeepTogether([story[-1], paragraph])
        else:
            story.append(KeepTogether([paragraph]))
        source.append({'type':'small', 'text':reference})

    def footer(canvas, doc):
        canvas.saveState();canvas.setFont('StudySerif',10)
        canvas.drawCentredString(4.25*inch,.45*inch,str(doc.page));canvas.restoreState()
    doc=SimpleDocTemplate(str(out),pagesize=(8.5*inch,11*inch),rightMargin=1*inch,leftMargin=1*inch,
                          topMargin=.8*inch,bottomMargin=.8*inch,title=TITLE,author='Sandip Nandi',
                          initialFontName='StudySerif')
    doc.build(story,onFirstPage=footer,onLaterPages=footer)
    (HERE/'manuscript.json').write_text(json.dumps({'title':TITLE,'subtitle':SUBTITLE,
        'authors':[{'name':'Sandip Nandi','affiliation':'Independent Researcher'}],
        'blocks':source},indent=2)+'\n')
    (HERE/'references.json').write_text(json.dumps([{'number':i,'text':t,'url':u} for i,(t,u) in enumerate(REFS,1)],indent=2)+'\n')
    manifest={'document_version':'1.8','date':'2026-11-09','code_commit':commit,'python':platform.python_version(),
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
