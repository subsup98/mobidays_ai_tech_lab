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

## Experiment 1 — Automatic speaker count (auto)

### Summary

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

### Result Files

| STT model | Generated transcript |
|---|---|
| `base` | `data/interim/model_comparison_auto/transcript_base_speakers-auto.json` |
| `small` | `data/interim/model_comparison_auto/transcript_small_speakers-auto.json` |
| `medium` | `data/interim/model_comparison_auto/transcript_medium_speakers-auto.json` |
| `large-v3` | `data/interim/model_comparison_large_auto/transcript_large-v3_speakers-auto.json` |

Raw summaries:

- `data/interim/model_comparison_auto/summary.csv`
- `data/interim/model_comparison_large_auto/summary.csv`

---

## Experiment 2 — Speaker count hint: num_speakers=3

Existing STT transcripts from Experiment 1 were reused. Only the diarization
stage was re-run with `num_speakers=3`. Script: `experiments/rerun_with_speaker_hint.py`.

### Summary

| STT model | Status | Diarization elapsed | Generated utterances | Reference utterances | Detected speakers | Reference speakers | Keyword recall | Missing keywords |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| `base` | completed | 160.6 sec | 79 | 37 | **3** | 3 | 1.000 | - |
| `small` | completed | 160.1 sec | 62 | 37 | **3** | 3 | 0.857 | `A/B` |
| `medium` | completed | 156.8 sec | 129 | 37 | **3** | 3 | 1.000 | - |
| `large-v3` | completed | 158.2 sec | 42 | 37 | **3** | 3 | 1.000 | - |

All 4 models correctly detected 3 speakers when the hint was provided. Keyword
recall per model is unchanged because the STT transcription text was not re-run.

### Result Files

| STT model | Generated transcript |
|---|---|
| `base` | `data/interim/model_comparison_3speakers/transcript_base_speakers-3.json` |
| `small` | `data/interim/model_comparison_3speakers/transcript_small_speakers-3.json` |
| `medium` | `data/interim/model_comparison_3speakers/transcript_medium_speakers-3.json` |
| `large-v3` | `data/interim/model_comparison_3speakers/transcript_large-v3_speakers-3.json` |

Raw summary: `data/interim/model_comparison_3speakers/summary.csv`

---

## Interpretation

### Experiment 1 (auto)

All tested STT models produced 2 detected speakers with automatic diarization.
Therefore, the 2-speaker result is not explained by using `small` Whisper. It is
more likely caused by the automatic diarization model grouping two similar or
short-speaking participants together (수아 and 채린 merged into `SPEAKER_00`).

### Experiment 2 (num_speakers=3)

Providing `num_speakers=3` resolved the speaker-count mismatch completely across
all 4 STT models. Diarization took approximately 157–161 seconds per run regardless
of STT model, because the same audio and the same pyannote model were used.

The STT model still affects transcript quality and segmentation:

- `base` is the best fast smoke-test option. It is the fastest successful model
  and captured all tracked domain keywords.
- `small` is not recommended for this sample because it missed `A/B` while taking
  longer than `base`.
- `medium` captured all tracked keywords but over-segmented the transcript and
  took more than twice as long as `base`.
- `large-v3` produced the closest utterance count to the reference transcript
  and captured all tracked keywords.

## Recommendation

Use `large-v3 + num_speakers=3` as the final representative quality setting
when the participant count is known in advance:

```text
STT_MODEL=large-v3
DIARIZATION_NUM_SPEAKERS=3
```

Use `base + num_speakers=3` for faster local smoke tests:

```text
STT_MODEL=base
DIARIZATION_NUM_SPEAKERS=3
```

If the participant count is unknown, keep automatic diarization and expose the
speaker-count mismatch in the STT review tab.

An additional non-pyannote diarization experiment was performed with NVIDIA NeMo
Sortformer (`nvidia/diar_sortformer_4spk-v1`). Sortformer also detected 2
speaker groups with automatic mode and produced the same mapping pattern:
지훈 separated, 수아 and 채린 merged. See `docs/speaker_mapping_comparison.md`.

The latest open-source pyannote path was also tested separately:
`pyannote.audio 4.0.4` with `pyannote/speaker-diarization-community-1`.
Community-1 also detected 2 speaker groups and mostly merged 수아 and 채린.
