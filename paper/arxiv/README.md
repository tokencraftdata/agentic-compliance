# TradeGauntlet paper: arXiv LaTeX version

`main.tex` is the v2.4 paper (10 November 2026) in the standard arXiv preprint
template ([arxiv.sty](https://github.com/kourgeorge/arxiv-style) by George Kour).
The text, tables, numbers and references are unchanged from the v2.4 PDF built by
`paper/offline_study/build.py`; only the layout differs. From now on, edit
`main.tex` directly.

| File | Contents |
| --- | --- |
| `main.tex` | The paper. References are an inline `thebibliography`, keys `r1`-`r29` in the v2.4 numbering. |
| `arxiv.sty` | The template style file, unmodified. |
| `figures/` | Figures as vector PDFs. |
| `figures.py` | Draws every figure from the saved results under `results/` (needs matplotlib and numpy): `python paper/arxiv/figures.py`. |
| `make_tex.py` | The one-off converter that produced the first `main.tex` from `paper/offline_study/manuscript.json`. Rerunning it overwrites `main.tex`; edit `main.tex` directly instead. |

## Build

Needs a TeX distribution with `latexmk` (TeX Live: `texlive-latex-recommended`,
`texlive-latex-extra`, `texlive-fonts-recommended`).

```sh
cd paper/arxiv
latexmk -pdf main.tex
```

The rendered copy is kept at `output/pdf/tradegauntlet_arxiv.pdf`. All fonts are
embedded and none are Type 3, as arXiv prefers.

## Uploading to arXiv

Upload `main.tex`, `arxiv.sty` and `figures/` (not the PDF): arXiv compiles the
source itself. See `paper/offline_study/ARXIV_PLAN.md` for the category,
endorsement and disclosure checklist.
