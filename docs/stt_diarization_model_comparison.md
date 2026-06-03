# STT and Diarization Model Comparison

## Purpose

The provided meeting has 3 reference speakers. During direct audio processing,
automatic diarization detected 2 speakers. This experiment checks whether the
speaker-count gap is caused by the Whisper STT model choice or by the diarization
stage itself.

## Input

- Audio: `data/raw/ko_meeting_3speakers_4min_faster.mp3`
- Reference transcript: `data/raw/ko_meeting_3speakers.json`
- Reference utterances: 37
- Reference speakers: 3
- Diarization mode: automatic speaker count
- Diarization model: `pyannote/speaker-diarization-3.1`
- STT engine: `faster-whisper`
- Device: CPU
- Compute type: int8

## Summary

| STT model | Status | Elapsed | Generated utterances | Reference utterances | Detected speakers | Reference speakers | Keyword recall | Missing keywords |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| `base` | completed | 196.45 sec | 79 | 37 | 2 | 3 | 1.000 | - |
| `small` | completed | 231.37 sec | 62 | 37 | 2 | 3 | 0.857 | `A/B` |
| `medium` | completed | 506.62 sec | 129 | 37 | 2 | 3 | 1.000 | - |
| `large-v3` | completed | 822.91 sec | 42 | 37 | 2 | 3 | 1.000 | - |

For the alternative diarization experiments, `large-v3` STT was not re-run.
The existing `large-v3` transcript was reused and only the diarization stage was
changed. Estimated end-to-end times are therefore calculated as:

| Combination | STT time | Diarization time | Estimated total |
|---|---:|---:|---:|
| `large-v3 + NeMo Sortformer` | 822.91 sec | 37.90 sec | 860.81 sec |
| `large-v3 + pyannote community-1` | 822.91 sec | 130.40 sec | 953.31 sec |

## Result Files

| STT model | Generated transcript |
|---|---|
| `base` | `data/interim/model_comparison_auto/transcript_base_speakers-auto.json` |
| `small` | `data/interim/model_comparison_auto/transcript_small_speakers-auto.json` |
| `medium` | `data/interim/model_comparison_auto/transcript_medium_speakers-auto.json` |
| `large-v3` | `data/interim/model_comparison_large_auto/transcript_large-v3_speakers-auto.json` |

Raw summaries:

- `data/interim/model_comparison_auto/summary.csv`
- `data/interim/model_comparison_large_auto/summary.csv`

## Interpretation

All tested STT models produced 2 detected speakers with automatic diarization.
Therefore, the 2-speaker result is not explained by using `small` Whisper. It is
more likely caused by the automatic diarization model grouping two similar or
short-speaking participants together.

The STT model still affects transcript quality and segmentation:

- `base` is the best fast smoke-test option. It is the fastest successful model
  and captured all tracked domain keywords.
- `small` is not recommended for this sample because it missed `A/B` while taking
  longer than `base`.
- `medium` captured all tracked keywords but over-segmented the transcript and
  took more than twice as long as `base`.
- `large-v3` produced the closest utterance count to the reference transcript
  and captured all tracked keywords. It took over 13 minutes on CPU, but it was
  selected as the final representative quality setting.

## Recommendation

Use `large-v3` as the final representative quality setting:

```text
STT_MODEL=large-v3
```

Use `base` only when a faster local smoke test is needed:

```text
STT_MODEL=base
```

Keep automatic diarization enabled, but expose the 2-vs-3 speaker mismatch in
the STT review and quality workflow. This is preferable to hard-coding the
speaker count because the assignment asks for robust pipeline behavior, not a
single sample-specific shortcut.

An additional non-pyannote diarization experiment was performed with NVIDIA NeMo
Sortformer (`nvidia/diar_sortformer_4spk-v1`). Sortformer also detected 2
speaker groups and produced the same mapping pattern: 지훈 separated, 수아 and
채린 merged. See `docs/speaker_mapping_comparison.md`.

The latest open-source pyannote path was also tested separately:
`pyannote.audio 4.0.4` with `pyannote/speaker-diarization-community-1`.
Community-1 also detected 2 speaker groups and mostly merged 수아 and 채린.
