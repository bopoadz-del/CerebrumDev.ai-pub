# Single-File Tool

A single self-contained `.html` deliverable. Each line below is a gate the
grader checks; a file that fails any line is not a single-file tool.

- ONE `.html` file. No build step, no server, no bundler. It must open from a
  phone's file browser and from a web viewer.
- File order: `<title>`, then `<meta name="claude:viewport-fit-sides"
  content="cover">`, then one Google Fonts `<link>`, then `<style>` (token
  blocks first), then markup, then ONE `<script>`. Script order inside that one
  `<script>`: DATA (const blocks, content only) → STATE (load/save) → ENGINE
  (pure functions) → VIEWS (render functions returning HTML strings) → EVENTS
  (bind on the rendered root) → `init`.
- Tokens: `:root` holds light values; `@media (prefers-color-scheme: dark)
  :root:not([data-theme="light"])` and `:root[data-theme="dark"]` hold the same
  dark values with `color-scheme: dark`; `body` sets background and color from
  tokens; no literal colours outside the token blocks.
- Layout: a sticky bar at `top: env(safe-area-inset-top,0px)`; `body::before`
  paints the inset band; gutters `padding-left/right: max(16px,
  env(safe-area-inset-*))`; main `max-width: 760px`; the tab strip is
  `role=tablist` with `aria-selected`; every flex/grid child that holds text has
  `min-width:0`; tables sit inside `.wrap{overflow-x:auto}`; the page works at
  400px with no horizontal scroll.
- Persistence: ONE localStorage key, versioned (`<app>.v1`); every read/write
  wrapped in try/catch; renders correctly with storage empty.
- Copy: `navigator.clipboard.writeText` inside the click handler, `.catch` → a
  textarea + `execCommand("copy")` fallback, toast on success.
- Dictation: `window.SpeechRecognition || webkitSpeechRecognition`; the button is
  hidden until supported; `continuous=true`; final results append to the target
  textarea and dispatch an `input` event so state saves; `onerror` hides the
  button with a toast.
- Export: copy-as-Markdown and copy-as-JSON always; download links ONLY when
  `location.protocol==="file:"`; restore from paste and from `<input type=file>`
  through FileReader.
- Destructive actions are two-tap (the button relabels "Tap again to confirm");
  no alert/confirm/prompt.
- Every form control has a stable id; buttons and tabs show a focus-visible
  outline; prefers-reduced-motion is respected.
- No external hosts except fonts.googleapis.com / fonts.gstatic.com; no fetch;
  no iframes.
- Content never in code: the DATA block is generated from YAML/JSON at build
  time; the ENGINE has no literal from the content.
