# TradeGauntlet paper: arXiv LaTeX source

`main.tex` is the paper, in the standard arXiv preprint template
([arxiv.sty](https://github.com/kourgeorge/arxiv-style) by George Kour). Edit
`main.tex` directly.

| File | Contents |
| --- | --- |
| `main.tex` | The paper. References are an inline `thebibliography`. |
| `arxiv.sty` | The template style file, unmodified. |
| `figures/` | Figures as vector PDFs. |
| `figures.py` | Draws every figure from the saved results under `results/` (needs matplotlib and numpy): `python paper/arxiv/figures.py`. |

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
source itself. Before uploading:

1. Proposed primary category: **q-fin.TR** (Trading and Market Microstructure).
   A first submission can require [endorsement](https://info.arxiv.org/help/endorsement.html).
2. Make the repository public, or the code link in the paper will not resolve.
3. Choose the paper's [license](https://info.arxiv.org/help/license/index.html);
   it cannot be changed for a posted version.
4. Check that the title, abstract and authors in the form match `main.tex`, and
   that the AI-assistance disclosure is current
   ([arXiv policy](https://info.arxiv.org/help/moderation/index.html#policy-for-authors-use-of-generative-ai-language-tools)).
