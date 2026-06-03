# Speaker Mapping Comparison

## Purpose

Automatic diarization detected 2 generated speakers, while the provided
reference transcript has 3 speakers:

- 지훈: 마케팅 팀장
- 수아: 퍼포먼스 마케터
- 채린: 콘텐츠 디자이너

This document checks which reference speaker each generated speaker label maps
to by matching generated utterance text against the provided transcript text.

## Method

The provided transcript does not include timestamps, so direct time alignment is
not possible. Instead, each generated utterance is matched to the most similar
reference utterance using normalized text similarity.

The matching output is written to:

- `data/interim/speaker_mapping/*_utterance_matches.csv`
- `data/interim/speaker_mapping/*_speaker_summary.json`

## Large-v3 Result

`large-v3` is the easiest result to inspect because its utterance count is
closest to the reference transcript.

Generated transcript:

`data/interim/model_comparison_large_auto/transcript_large-v3_speakers-auto.json`

Detailed match file:

`data/interim/speaker_mapping/transcript_large-v3_speakers-auto_utterance_matches.csv`

Summary:

| Generated speaker | Matched reference speakers | Dominant mapping | Dominant share | Avg text similarity |
|---|---|---|---:|---:|
| `SPEAKER_01` | 지훈 15 | 지훈 | 1.000 | 0.850 |
| `SPEAKER_00` | 수아 17, 채린 10 | 수아 + 채린 merged | 0.630 | 0.852 |

Reference-to-generated view:

| Reference speaker | Generated speaker labels |
|---|---|
| 지훈 | `SPEAKER_01` 15 |
| 수아 | `SPEAKER_00` 17 |
| 채린 | `SPEAKER_00` 10 |

## Cross-Model Pattern

The same pattern appears across all tested STT models.

| STT model | `SPEAKER_01` mainly maps to | `SPEAKER_00` mainly maps to | Interpretation |
|---|---|---|---|
| `base` | 지훈 | 수아 + 채린 | 수아/채린 merged |
| `small` | 지훈 | 수아 + 채린 | 수아/채린 merged |
| `medium` | 지훈 | 수아 + 채린 | 수아/채린 merged |
| `large-v3` | 지훈 | 수아 + 채린 | 수아/채린 merged |

## Alternative Diarization Model: NeMo Sortformer

To check whether the issue is specific to pyannote, an alternative diarization
model was tested in a separate virtual environment:

- Environment: `.venv-nemo`
- Toolkit: NVIDIA NeMo
- Model: `nvidia/diar_sortformer_4spk-v1`
- Diarization time: about 37.90 sec
- Estimated end-to-end time with reused `large-v3` STT: 860.81 sec
- Input audio: `data/interim/ko_meeting_3speakers_16k.wav`
- Raw diarization output: `data/interim/nemo_sortformer/sortformer_raw_segments.json`
- Generated transcript: `data/interim/nemo_sortformer/transcript_large-v3_sortformer.json`
- Speaker mapping output:
  `data/interim/speaker_mapping/transcript_large-v3_sortformer_speaker_summary.json`

Sortformer result:

| Generated speaker | Matched reference speakers | Dominant mapping | Dominant share | Avg text similarity |
|---|---|---|---:|---:|
| `SORTFORMER_00` | 지훈 15 | 지훈 | 1.000 | 0.850 |
| `SORTFORMER_01` | 수아 17, 채린 10 | 수아 + 채린 merged | 0.630 | 0.852 |

Reference-to-generated view:

| Reference speaker | Sortformer speaker labels |
|---|---|
| 지훈 | `SORTFORMER_00` 15 |
| 수아 | `SORTFORMER_01` 17 |
| 채린 | `SORTFORMER_01` 10 |

The alternative model therefore showed the same high-level behavior as pyannote:
it separated 지훈, but merged 수아 and 채린.

## Latest pyannote Open-Source Model: Community-1

The latest open-source pyannote diarization stack was tested in a separate
virtual environment so the main project environment would remain stable:

- Environment: `.venv-pyannote4`
- Library: `pyannote.audio 4.0.4`
- Model: `pyannote/speaker-diarization-community-1`
- Diarization time: about 130.40 sec
- Estimated end-to-end time with reused `large-v3` STT: 953.31 sec
- Input audio: `data/interim/ko_meeting_3speakers_16k.wav`
- Raw diarization output:
  `data/interim/pyannote4_community/community1_segments.json`
- Generated transcript:
  `data/interim/pyannote4_community/transcript_large-v3_community1.json`
- Speaker mapping output:
  `data/interim/speaker_mapping/transcript_large-v3_community1_speaker_summary.json`

Community-1 result:

| Generated speaker | Matched reference speakers | Dominant mapping | Dominant share | Avg text similarity |
|---|---|---|---:|---:|
| `COMMUNITY1_00` | 지훈 15, 수아 1 | 지훈 | 0.938 | 0.856 |
| `COMMUNITY1_01` | 수아 16, 채린 10 | 수아 + 채린 merged | 0.615 | 0.849 |

Reference-to-generated view:

| Reference speaker | Community-1 speaker labels |
|---|---|
| 지훈 | `COMMUNITY1_00` 15 |
| 수아 | `COMMUNITY1_01` 16, `COMMUNITY1_00` 1 |
| 채린 | `COMMUNITY1_01` 10 |

Community-1 therefore also detected 2 speaker groups. It separated 지훈, but
still merged most 수아 and 채린 turns.

## Interpretation

The diarization models consistently separated 지훈 from the other participants,
but grouped 수아 and 채린 into a single generated speaker label.

This explains why automatic diarization detected 2 speakers:

```text
SPEAKER_01 -> 지훈
SPEAKER_00 -> 수아 + 채린
```

The issue is not mainly caused by the selected STT model or by a single
pyannote-specific failure. pyannote 3.1, NeMo Sortformer, and pyannote
Community-1 all produced the same broad 2-speaker grouping on this short sample.
The likely cause is that 수아 and 채린 have short or similar-sounding turns in
this audio, making fully automatic speaker clustering difficult without
additional metadata or review.

## Submission Framing

Do not hide this mismatch by hard-coding the speaker count. Keep automatic
diarization as the primary path and surface the mismatch in the STT review and
quality workflow.

This is useful for evaluation because it shows:

- direct STT and diarization were actually attempted
- generated output is compared against the provided transcript
- diarization risk is made visible instead of silently trusted
- downstream action item extraction remains reviewable through source utterances
