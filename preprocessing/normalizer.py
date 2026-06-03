from __future__ import annotations

from models import Participant, Transcript


ROLE_BY_INDEX = {
    0: "team_lead",
    1: "performance_marketer",
    2: "content_designer",
}


def normalize_speakers(transcript: Transcript) -> Transcript:
    speakers = sorted({utterance.speaker_raw for utterance in transcript.utterances})
    speaker_to_role = {
        speaker: ROLE_BY_INDEX.get(index, f"speaker_{index:02d}")
        for index, speaker in enumerate(speakers)
    }

    participants = [
        Participant(
            participant_id=participant.participant_id,
            meeting_id=participant.meeting_id,
            speaker_raw=participant.speaker_raw,
            speaker_normalized=speaker_to_role.get(
                participant.speaker_raw,
                participant.speaker_raw.lower(),
            ),
            role=speaker_to_role.get(participant.speaker_raw),
            confidence=0.6,
        )
        for participant in transcript.participants
    ]

    utterances = [
        utterance.model_copy(
            update={
                "speaker_normalized": speaker_to_role.get(
                    utterance.speaker_raw,
                    utterance.speaker_raw.lower(),
                )
            }
        )
        for utterance in transcript.utterances
    ]

    return transcript.model_copy(
        update={
            "participants": participants,
            "utterances": utterances,
        }
    )


def remove_consecutive_duplicates(transcript: Transcript) -> Transcript:
    kept = []
    previous_key: tuple[str, str] | None = None

    for utterance in transcript.utterances:
        key = (utterance.speaker_raw, utterance.text.strip())
        if key == previous_key:
            continue
        kept.append(utterance)
        previous_key = key

    return transcript.model_copy(update={"utterances": kept})
