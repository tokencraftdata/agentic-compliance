# TradeGauntlet paper: arXiv LaTeX source

`main.tex` is **TradeGauntlet: Evaluating Compliance Controls for Autonomous
Trading Agents**, using a community preprint template
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

The revised PDF was also built with Tectonic 0.17.0 (XeTeX):

```sh
tectonic --keep-logs main.tex
```

The rendered copy is kept at `output/pdf/tradegauntlet_arxiv.pdf`. Check the build
log for undefined references and inspect the PDF after each edit. Fonts should be
embedded and figures readable at the paper's final size.

## Uploading to arXiv

The upload bundle is `output/arxiv/tradegauntlet_arxiv_source.tar.gz`. It contains
`main.tex`, `arxiv.sty`, and the referenced PDFs in `figures/`, with `main.tex` at
the archive root. References are included in the source; no bibliography build
step or experiment run is needed. The bundle excludes build logs and the rendered
paper PDF.

Upload the source bundle and select XeLaTeX to use the same engine family as the
checked local build. The source also uses standard pdfLaTeX-compatible packages.
Inspect the PDF arXiv compiles before finalizing the submission. See
[arXiv's TeX submission guidance](https://info.arxiv.org/help/submit_tex.html).

The current revision changes the title, framing, and interpretation of existing
results. Experiment code and evidence remain pinned to `a0d736f` in the paper.
