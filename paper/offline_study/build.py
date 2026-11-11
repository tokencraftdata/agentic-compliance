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
SENSITIVITY = ROOT / 'results/sensitivity-v1'
TITLE = 'TradeGauntlet: Testing the Limits of Trading Compliance Controls'
SUBTITLE = 'Scripted experiments on order splitting and cancellation timing'
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
    ('Anri Gu et al. The Effect of Liquidity on the Spoofability of Financial Markets. ICAIF, 2024, pp. 239-247.', 'https://doi.org/10.1145/3677052.3698634'),
    ('Álvaro Cartea, Sebastian Jaimungal, and Yixuan Wang. Spoofing and Price Manipulation in Order-Driven Markets. Applied Mathematical Finance 27(1-2), 2020, pp. 67-98.', 'https://doi.org/10.1080/1350486X.2020.1726783'),
    ('Xuan Tao, Andrew Day, Lan Ling, and Samuel Drapeau. On detecting spoofing strategies in high-frequency trading. Quantitative Finance 22(8), 2022, pp. 1405-1425.', 'https://doi.org/10.1080/14697688.2022.2059390'),
    ('Jianqiang Chen, Pei-Fang Hsieh, and J. Jimmy Yang. Order spoofing, price impact, and market quality. Pacific-Basin Finance Journal 97, 2026, article 103077.', 'https://doi.org/10.1016/j.pacfin.2026.103077'),
    ('Commodity Futures Trading Commission. Antidisruptive Practices Authority: Interpretive guidance and policy statement. Federal Register 78, 2013, pp. 31890-31897.', 'https://www.cftc.gov/LawRegulation/FederalRegister/FinalRules/2013-12365.html'),
    ('European Commission. Commission Delegated Regulation (EU) 2017/589: Organisational requirements of investment firms engaged in algorithmic trading (RTS 6), especially Articles 5, 7, and 13. Official Journal L 87, 2017, pp. 417-448.', 'https://eur-lex.europa.eu/eli/reg_del/2017/589/oj/eng'),
    ("Robinhood. Robinhood Puts the Power of Hedge Funds in Every Trader's Pocket. Company announcement, September 29, 2026. Accessed November 10, 2026.", 'https://robinhood.com/newsroom/hood-summit-2026/'),
    ('Robinhood. Trading with your agent. Product documentation, accessed November 10, 2026.', 'https://robinhood.com/us/en/support/articles/trading-with-your-agent/'),
    ('Avinash Kumar Pandey and Swati Rajwal. Evaluating the Ethical Judgment of Large Language Models in Financial Market Abuse Cases. Proceedings of the 6th ACM International Conference on AI in Finance (ICAIF), 2025, pp. 132-140.', 'https://doi.org/10.1145/3768292.3770439'),
    ('Wenliang Huang, Junkai Li, Zengyi Yu, and Xiangjie Kong. RISCTrade: A Dynamic Multi-Market Benchmark for Risk-Mandate Compliance of Large Language Models in Closed-Loop Trading. Advanced Intelligent Computing Technology and Applications (ICIC 2026), LNCS 16648, Springer, 2027, pp. 360-370. First online July 14, 2026.', 'https://doi.org/10.1007/978-981-92-3400-4_30'),
    ('Haohang Li et al. INVESTORBENCH: A Benchmark for Financial Decision-Making Tasks with LLM-based Agent. Proceedings of ACL 2025, Volume 1: Long Papers, pp. 2509-2525.', 'https://doi.org/10.18653/v1/2025.acl-long.126'),
    ('Wentao Zhang et al. A Multimodal Foundation Agent for Financial Trading: Tool-Augmented, Diversified, and Generalist. Proceedings of the 30th ACM SIGKDD Conference on Knowledge Discovery and Data Mining (KDD), 2024.', 'https://doi.org/10.1145/3637528.3671801'),
    ('Haochen Luo et al. QFinZero: A Unified Financial Toolchain for LLM-Based Trading Agents. Proceedings of ACL 2026, Volume 3: System Demonstrations, pp. 68-77.', 'https://doi.org/10.18653/v1/2026.acl-demo.7')
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


def sensitivity_figures(data, folder):
    thresholds = [0.1, 0.2, 0.25, 0.3, 0.4, 0.5]
    caps = [1, 2, 3, 4, 6]
    def cell(**kw):
        return next(c for c in data['cells'] if all(c[k] == v for k, v in kw.items()))
    delta, cost = [], []
    for cap in caps:
        dr, cr = [], []
        for threshold in thresholds:
            rows = [r for r in data['paired_differences'] if r['cohort'] == 'cap_response'
                    and r['strategy'] == 'split_6x10' and r['gate'] == 'aggregate'
                    and r['cap'] == cap and r['threshold'] == threshold]
            dr.append(np.mean([r['response_wealth_difference'] for r in rows]))
            off = cell(cohort='cap_response', strategy='large_market_maker', gate='off',
                       threshold=threshold, aware=True)['filled_lots']
            gated = cell(cohort='cap_response', strategy='large_market_maker', gate='aggregate',
                         threshold=threshold, cap=cap, aware=True)['filled_lots']
            cr.append(100 * (off - gated) / off)
        delta.append(dr); cost.append(cr)
    fig, axes = plt.subplots(1, 2, figsize=(6.6, 3.1), layout='constrained')
    for ax, values, title, cmap in zip(axes, [delta, cost],
            ['Split probe: wealth difference', 'Maker: executed-volume loss (%)'], ['YlOrRd', 'RdBu_r']):
        limits = {'vmin': -100, 'vmax': 100} if cmap == 'RdBu_r' else {}
        im = ax.imshow(values, aspect='auto', cmap=cmap, **limits)
        ax.set_xticks(range(len(thresholds)), [str(t) for t in thresholds], fontsize=8)
        ax.set_yticks(range(len(caps)), [str(m) for m in caps])
        ax.set_xlabel('Response threshold'); ax.set_ylabel('Aggregate cap / reference depth')
        ax.set_title(title, fontsize=9)
        low, high = im.get_clim()
        for i in range(len(caps)):
            for j in range(len(thresholds)):
                value = values[i][j]
                dark = abs(value) > 60 if cmap == 'RdBu_r' else value > low + .65 * (high-low)
                ax.text(j, i, f'{value:.1f}', ha='center', va='center', fontsize=7,
                        color='white' if dark else '#202020')
    fig.savefig(folder/'cap_response.png', dpi=240); fig.savefig(folder/'cap_response.svg'); plt.close(fig)
    fig, axes = plt.subplots(1, 2, figsize=(6.6, 2.65), layout='constrained')
    windows = [120, 300, 900, 1800]
    for ax, gate in zip(axes, ['legacy', 'aggregate']):
        for horizon, color in [(720, '#aa5b4a'), (3600, '#285675')]:
            off = cell(cohort='throttle_horizon', gate='off', horizon=horizon)['filled_lots']
            rows = [cell(cohort='throttle_horizon', gate=gate, horizon=horizon, window=w, throttle=True) for w in windows]
            rows.append(cell(cohort='throttle_horizon', gate=gate, horizon=horizon, throttle=False))
            ax.plot(range(4), [100*r['filled_lots']/off for r in rows[:4]], marker='o', color=color,
                    label=f'{horizon//60}-minute session', linewidth=1.5, markersize=4)
            ax.scatter([4], [100*rows[4]['filled_lots']/off], marker='D', color=color, s=20)
        ax.set_xticks(range(5), ['120','300','900','1800','off'], fontsize=8)
        ax.set_xlabel('Throttle window (seconds; off = disabled)')
        ax.set_title(gate.capitalize()+' gate', fontsize=9)
        ax.set_ylabel('Volume / ungated mean (%)'); ax.set_ylim(0, 115)
        ax.axhline(100, color='#bbbbbb', linestyle=':', linewidth=1)
        ax.spines[['top','right']].set_visible(False); ax.legend(fontsize=7, frameon=False, loc='lower left')
    fig.savefig(folder/'throttle_horizon.png', dpi=240); fig.savefig(folder/'throttle_horizon.svg'); plt.close(fig)


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
    sensitivity = json.loads((SENSITIVITY/'summary.json').read_text())
    boundary = json.loads((ROOT/'results/boundary-v1/summary.json').read_text())
    reporting = json.loads((ROOT/'results/reporting-v2_3.json').read_text())
    sensitivity_figures(sensitivity, figs)
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
    def table(headers, rows, fractions, rich=False):
        data = [[Paragraph('<b>'+escape(str(x))+'</b>', styles['cell']) for x in headers]]
        data += [[Paragraph(str(x) if rich else escape(str(x)), styles['cell']) for x in row] for row in rows]
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
    p('10 November 2026','subtitle')
    from content import compose
    compose(p, table, figure, summary, sensitivity, boundary, reporting, commit)

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
    manifest={'document_version':'2.4','date':'2026-11-10','code_commit':commit,'python':platform.python_version(),
              'reportlab':reportlab.Version,'matplotlib':matplotlib.__version__,
              'source_sha256':digest(Path(__file__)),'content_source_sha256':digest(HERE/'content.py'),'evidence_manifest_sha256':digest(DATA/'manifest.json'),
              'summary_sha256':digest(DATA/'summary.json'),
              'sensitivity_manifest_sha256':digest(SENSITIVITY/'manifest.json'),
              'sensitivity_summary_sha256':digest(SENSITIVITY/'summary.json'),'pdf_sha256':digest(out),
              'boundary_manifest_sha256':digest(ROOT/'results/boundary-v1/manifest.json'),
              'reporting_sha256':digest(ROOT/'results/reporting-v2_3.json'),
              'pdf_bytes':out.stat().st_size,'manuscript_sha256':digest(HERE/'manuscript.json')}
    (HERE/'document_manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print(json.dumps({'pdf':str(out),'bytes':out.stat().st_size},indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--code-commit',required=True)
    args=parser.parse_args()
    if len(args.code_commit)!=40 or any(c not in '0123456789abcdef' for c in args.code_commit):
        parser.error('Use a full immutable 40-character source commit SHA')
    build(args.code_commit)
