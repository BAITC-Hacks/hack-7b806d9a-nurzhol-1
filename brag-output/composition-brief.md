# WindOps composition brief

Build a 24-second, 1920×1080 landscape Russian demo using the companion Hyperframes skills. Follow brag-plan.md. Show the real archive product, not an invented dashboard. Recreate the central chart and controls faithfully at video scale from source and real data; include an actual captured UI screen in the closing composition. All timestamps UTC+5, power normalized 0–1. Weather is NOAA GFS; OpenAI explains, the saved empirical curve calculates numbers.

Use one seekable composition with four scenes and distinct holds. Sequential tool stages are an editorial reconstruction of the real workflow; timing is condensed. Comparison uses 31 January vs 1 February on the 24 shared hours of 2 February. Peak values and event timestamps must match the saved JSON/CSV. Do not show unverified accuracy or fictitious user counts. No voiceover.

Runtime: Hyperframes 0.8.64, local render, no hosted upload. Use local assets and fonts. Select a line drawing primitive from the Hyperframes catalog, adapt to actual series and preserve the dashed second line. Low-risk UI clicks and one soft reveal from brag assets. Music: happy-beats-business-moves-vol-12-by-ende-dot-app.mp3. Cue preset source: brag/assets/music/cues/happy-beats-business-moves-vol-12-by-ende-dot-app.music-cues.json.

Deliver brag.mp4, strongest readable hero poster brag.jpg baked into frame zero, share-copy.txt and editable composition. Check must pass with zero errors; inspect scene frames and verify H.264, yuv420p, duration, audio and final hold. README links to these files. Preserve source/licensing credits with composition.

## Delivery verification

Hyperframes 0.8.64 check passed: zero errors across runtime, layout and contrast; 113 contrast checks passed. Four advisory lint warnings recommend sub-compositions; the four-scene monolithic composition is intentional. Examined hook, chart, comparison/answer and final UI frames. Render: 24.000 s, 1920×1080, 30 fps, 720 frames, H.264 yuv420p + AAC. Poster selected from the fully settled forecast scene at 9.5 s and baked as frame zero; remaining scene timing is unchanged. Audio peak measured at −7.4 dBFS. Local README links verified.

Reproduce poster delivery after rendering (from composition/):

```sh
ffmpeg -y -ss 9.5 -i ../brag.mp4 -frames:v 1 -q:v 2 ../brag.jpg
ffmpeg -y -i ../brag.mp4 -i ../brag.jpg \
  -filter_complex "[0:v][1:v]overlay=0:0:enable='eq(n,0)'[v]" \
  -map "[v]" -map "0:a?" -c:v libx264 -crf 18 -preset slow \
  -pix_fmt yuv420p -c:a copy -movflags +faststart ../brag.poster.mp4
mv ../brag.poster.mp4 ../brag.mp4
```
