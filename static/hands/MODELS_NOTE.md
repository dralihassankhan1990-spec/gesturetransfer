# MediaPipe Hands bundle (excluded from git)

`static/hands/` normally contains the bundled MediaPipe Hands solution files
(`hands_solution_simd_wasm_bin.wasm`, `hands_solution_packed_assets.data`,
`hands_solution_simd_wasm_bin.js`) so the phone client works offline.

These files (6 MB + 4.3 MB) exceed git push size limits and are not stored
here. Get them in one of these ways:

1. From the original project folder on Google Drive ("My Project for Muse" → GestureTransfer → static/hands/), or
2. From the MediaPipe Hands solution release: https://google.github.io/mediapipe/solutions/hands.html

Place the files directly in `static/hands/` — no code changes needed.
