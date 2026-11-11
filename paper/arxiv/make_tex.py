"""One-off port of the v2.4 manuscript into the arXiv LaTeX template.

Reads paper/offline_study/manuscript.json (the exact text, tables and captions of
the v2.4 PDF) and writes paper/arxiv/main.tex plus vector figures. After this
port, main.tex is the editable source of the paper; rerunning this script
overwrites it.

Usage, from the repository root:
    python paper/arxiv/make_tex.py
    cd paper/arxiv && latexmk -pdf main.tex
"""
import html
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
SOURCE = ROOT / 'paper/offline_study'

CHARS = {'≤': r'$\leq$', '≥': r'$\geq$', 'ā': r'\={a}', 'ņ': r'\c{n}', 'š': r'\v{s}',
         'Á': r"\'{A}", 'é': r"\'{e}", 'í': r"\'{i}"}
FIGURES = {'Figure 1.': 'cap_response', 'Figure 2.': 'throttle_horizon', 'Figure 3.': 'cadence'}
# Column widths from build.py's table() calls are not stored in manuscript.json;
# these give the long text columns room.
COLUMNS = {
    'Component': 'lX', 'Signal': 'lX', 'Sensitivity analysis': 'lXr',
}


def escape(text):
    text = html.unescape(text)
    text = text.replace('\\', r'\textbackslash{}')
    for ch in '&%$#_{}':
        text = text.replace(ch, '\\' + ch)
    for ch, rep in CHARS.items():
        text = text.replace(ch, rep)
    return text


def inline(text, cite=True, code=True):
    """Convert ReportLab paragraph markup to LaTeX."""
    text = re.sub(r'<sub>(.*?)</sub>', lambda m: '\x00SUB' + m.group(1) + '\x00', text)
    text = text.replace('<br/>', '\x00BR\x00')
    links = re.findall(r'<link href="([^"]+)"[^>]*>(.*?)</link>', text)
    text = re.sub(r'<link href="[^"]+"[^>]*>.*?</link>', '\x00LINK\x00', text)
    text = escape(text)
    text = re.sub(r'\x00SUB(.*?)\x00', r'\\textsubscript{\1}', text)
    text = text.replace('\x00BR\x00', r'\\')
    if code:
        # Module and script names such as paper\_study.sensitivity, pilot.revision or vwap\_executor.
        text = re.sub(r'\b((?:pilot|paper\\_study|scripts|agent\\_study)\.[a-z0-9\\_]+[a-z0-9]'
                      r'|[a-z][a-z0-9]*(?:\\_[a-z0-9]+)+)\b',
                      lambda m: r'\texttt{' + m.group(1) + '}', text)
    for url, label in links:
        text = text.replace('\x00LINK\x00', r'\href{' + url + '}{' + escape(label) + '}', 1)
    if cite:
        text = re.sub(r'\[(\d{1,2}(?:,\s*\d{1,2})*)\]',
                      lambda m: r'~\cite{' + ','.join('r' + n.strip() for n in m.group(1).split(',')) + '}',
                      text)
        text = text.replace(' ~\\cite', '~\\cite')
    return text


def table(block, caption=None):
    headers, rows = block['headers'], block['rows']
    spec = COLUMNS.get(headers[0], 'l' + 'X' * 0 + 'l' * (len(headers) - 1))
    if len(spec) != len(headers):
        spec = 'l' * len(headers)
    env = 'tabularx' if 'X' in spec else 'tabular'
    width = '{\\linewidth}' if env == 'tabularx' else ''
    out = ['\\begin{table}[htbp]', '\\centering', '\\small',
           f'\\begin{{{env}}}{width}{{{spec}}}', '\\toprule',
           ' & '.join('\\textbf{' + inline(str(h), cite=False, code=False) + '}' for h in headers) + ' \\\\',
           '\\midrule']
    for row in rows:
        out.append(' & '.join(inline(str(c), cite=False) for c in row) + ' \\\\')
    out += ['\\bottomrule', f'\\end{{{env}}}']
    if caption:
        out.append(caption)
    out.append('\\end{table}')
    return '\n'.join(out)


def caption(text):
    label, body = re.match(r'(Table \w+\.|Figure \d+\.)\s*(.*)', text).groups()
    return label, '\\caption{' + inline(body) + '}'


def references():
    refs = json.loads((SOURCE / 'references.json').read_text())
    out = ['\\small', '\\begin{thebibliography}{99}']
    for r in refs:
        url = r['url']
        out.append(f"\\bibitem{{r{r['number']}}} {inline(r['text'], cite=False, code=False)}\n"
                   f"\\url{{{url}}}")
    out.append('\\end{thebibliography}')
    return '\n\n'.join(out)


def make_figures():
    """Redraw the three paper figures as vector PDFs with the v2.4 plotting code."""
    sys.path.insert(0, str(SOURCE))
    import matplotlib
    matplotlib.use('Agg')
    matplotlib.rcParams['pdf.fonttype'] = 42  # embed TrueType, not Type 3
    from matplotlib.figure import Figure
    import build  # paper/offline_study/build.py
    folder = HERE / 'figures'
    folder.mkdir(exist_ok=True)
    original = Figure.savefig

    def savefig(self, fname, *args, **kwargs):
        path = Path(fname)
        if path.suffix == '.png':
            original(self, folder / (path.stem + '.pdf'), *args, **{**kwargs, 'dpi': None})
    Figure.savefig = savefig
    try:
        build.HERE = HERE  # build.figures() writes to HERE / 'figures'
        summary = json.loads((build.DATA / 'summary.json').read_text())
        build.figures(summary)
        sensitivity = json.loads((build.SENSITIVITY / 'summary.json').read_text())
        build.sensitivity_figures(sensitivity, folder)
    finally:
        Figure.savefig = original
    (folder / 'throttle.pdf').unlink(missing_ok=True)  # drawn by build.py but not in the paper


def main():
    manuscript = json.loads((SOURCE / 'manuscript.json').read_text())
    blocks = manuscript['blocks']
    body, abstract, i = [], [], 0
    in_abstract = False
    while i < len(blocks):
        b = blocks[i]
        kind, text = b['type'], b.get('text', '')
        nxt = blocks[i + 1] if i + 1 < len(blocks) else {}
        if kind in ('title', 'subtitle'):
            pass
        elif kind == 'h1' and text == 'Abstract':
            in_abstract = True
        elif kind == 'h1' and text == 'References':
            break
        elif kind in ('h1', 'h2'):
            in_abstract = False
            title = re.sub(r'^[\d.]+\s+', '', text)
            body.append(('\\section{' if kind == 'h1' else '\\subsection{') + inline(title, code=False) + '}')
        elif kind == 'body':
            (abstract if in_abstract else body).append(inline(text))
        elif kind == 'table':
            cap = None
            if nxt.get('type') == 'caption' and nxt['text'].startswith('Table'):
                label, cap = caption(nxt['text'])
                i += 1
                num = label.split()[1].rstrip('.')
                if not num.isdigit():  # Tables 5a and 5b keep their letters
                    body.append(f'\\setcounter{{table}}{{{int(num[:-1]) - 1}}}'
                                f'\\renewcommand{{\\thetable}}{{{num}}}')
            else:
                # Uncaptioned specification tables in v2.4 stay uncaptioned.
                b = dict(b)
            body.append(table(b, cap))
            if cap and not label.split()[1].rstrip('.').isdigit():
                body.append('\\renewcommand{\\thetable}{\\arabic{table}}')
        elif kind == 'caption' and text.startswith('Figure'):
            label, cap = caption(text)
            body.append('\n'.join(['\\begin{figure}[htbp]', '\\centering',
                                   f'\\includegraphics[width=\\linewidth]{{figures/{FIGURES[label]}.pdf}}',
                                   cap, '\\end{figure}']))
        else:
            raise ValueError(f'unhandled block {i}: {kind} {text[:60]}')
        i += 1

    tex = r"""%% TradeGauntlet paper, arXiv template (arxiv.sty by George Kour, github.com/kourgeorge/arxiv-style).
%% Ported from v2.4 (10 November 2026) by paper/arxiv/make_tex.py. Edit this file directly from now on.
\documentclass{article}

\usepackage{arxiv}

\usepackage[utf8]{inputenc}
\usepackage[T1]{fontenc}
\usepackage{hyperref}
\usepackage{url}
\usepackage{booktabs}
\usepackage{tabularx}
\usepackage{amsfonts}
\usepackage{amsmath}
\usepackage{microtype}
\usepackage{courier}
\usepackage[font=small,skip=6pt]{caption}
\usepackage{graphicx}
\usepackage{xcolor}
\hypersetup{colorlinks=true, linkcolor=black, citecolor=blue!50!black, urlcolor=blue!50!black,
  pdftitle={TradeGauntlet: Testing the Limits of Trading Compliance Controls},
  pdfauthor={Sandip Nandi}}

\title{TradeGauntlet: Testing the Limits of Trading Compliance Controls\\[4pt]
  \large Scripted experiments on order splitting and cancellation timing}
\date{10 November 2026}
\author{Sandip Nandi\\
  Independent Researcher}
\renewcommand{\shorttitle}{TradeGauntlet}
\renewcommand{\headeright}{Preprint}
\renewcommand{\undertitle}{Preprint}

\begin{document}
\maketitle

\begin{abstract}
""" + '\n\n'.join(abstract) + r"""
\end{abstract}

\keywords{trading compliance \and market surveillance \and spoofing \and limit order book simulation \and AI trading agents}

""" + '\n\n'.join(body) + '\n\n' + references() + '\n\n\\end{document}\n'
    (HERE / 'main.tex').write_text(tex)
    make_figures()
    print(f'wrote {HERE / "main.tex"}')


if __name__ == '__main__':
    main()
