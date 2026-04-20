#!/bin/bash
# Build production Tailwind CSS for DecisionsAI kanban
# Run this from the project root: bash static/shared/css/build-tailwind.sh
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../../../.." && pwd)"
TEMPLATE_DIR="$PROJECT_DIR/distr/gui/web/templates"
STATIC_DIR="$PROJECT_DIR/distr/gui/web/static"
OUTPUT="$SCRIPT_DIR/tailwind-prod.css"

# Create temp build directory
BUILD_DIR=$(mktemp -d)
trap "rm -rf $BUILD_DIR" EXIT

# Create safelist HTML for dynamic classes not detectable by scanner
cat > "$BUILD_DIR/safelist.html" << 'SAFELIST'
<!--
  SAFELIST: Dynamic Tailwind classes used via classList.add/remove in JavaScript
  These are not detectable by the Tailwind content scanner because they're
  constructed as string arguments rather than full class attributes in templates.
-->
<div class="bg-[#25D366]/20 bg-[#25D366]/10 bg-[#25D366]/40 border-[#25D366]/50 border-[#25D366] text-[#25D366] border-white/25 text-gray-200 border-l-2 hover:bg-[#f97316]/30 hover:text-[#fb923c] bg-[#f97316]/15 bg-[#f97316]/30 text-[#fb923c]"></div>
SAFELIST

# Tailwind config
cat > "$BUILD_DIR/tailwind.config.js" << 'ENDCONFIG'
/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    "BUILD_DIR/safelist.html",
    "TEMPLATE_DIR/**/*.html",
    "STATIC_DIR/**/*.js",
  ],
  theme: {
    extend: {},
  },
  plugins: [],
}
ENDCONFIG

# Replace placeholders
sed -i.bak "s|BUILD_DIR|$BUILD_DIR|g; s|TEMPLATE_DIR|$TEMPLATE_DIR|g; s|STATIC_DIR|$STATIC_DIR|g" "$BUILD_DIR/tailwind.config.js"

# Input CSS
cat > "$BUILD_DIR/input.css" << 'ENDCSS'
@tailwind base;
@tailwind components;
@tailwind utilities;
ENDCSS

# Check for tailwindcss
if command -v npx &>/dev/null; then
    npx tailwindcss -i "$BUILD_DIR/input.css" -o "$OUTPUT" --minify \
      -c "$BUILD_DIR/tailwind.config.js"
elif [ -f "$HOME/.npm-global/bin/tailwindcss" ]; then
    "$HOME/.npm-global/bin/tailwindcss" -i "$BUILD_DIR/input.css" -o "$OUTPUT" --minify \
      -c "$BUILD_DIR/tailwind.config.js"
else
    echo "ERROR: tailwindcss not found. Install with: npm install -g tailwindcss"
    exit 1
fi

echo "✓ Built $OUTPUT ($(wc -c < "$OUTPUT") bytes)"