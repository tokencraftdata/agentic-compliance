#!/bin/sh
# Renders each SVG to a 2x PNG with headless Chrome (sharp on Medium and Substack).
cd "$(dirname "$0")" || exit 1
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
for svg in finbot-architecture finbot-gate finbot-domain finbot-security finbot-system; do
  size=$(sed -n 's/.*<svg[^>]*width="\([0-9]*\)" height="\([0-9]*\)".*/\1,\2/p' "$svg.svg" | head -1)
  "$CHROME" --headless=new --disable-gpu --hide-scrollbars --force-device-scale-factor=2 \
    --window-size="$size" --screenshot="$PWD/$svg.png" "file://$PWD/$svg.svg" >/dev/null 2>&1
  echo "$svg.png ($size at 2x)"
done
