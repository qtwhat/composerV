"""Core domain models, shared across index / story / render.

Design rules carried from the plan:
- All timecodes are SOURCE-MEDIA SECONDS (float). Conversion to FCPXML integer-frame
  rationals happens only at export, against the IntentionList's timeline_fps.
- A Moment is a reusable atom that lives in the archive index and is SHARED across all
  Story branches. A Story references moments by id; it never copies media metadata.
- The IntentionList is the single contract that both the live preview engine and the
  FCPXML emitter consume. A Story compiles down to it; nothing compiles back up.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator


class Moment(BaseModel):
    """A reusable atom of footage: a source clip plus an in/out range, with the semantics
    and metadata the index extracted. Story beats are filled with moments."""

    id: str
    source_clip_id: str
    in_sec: float
    out_sec: float

    # Semantics + metadata (filled by index/; optional so the core works without them).
    caption: str = ""
    transcript_text: str = ""
    people: list[str] = Field(default_factory=list)
    place: str | None = None
    starred_by_user: bool = False
    importance_score: float = 0.0
    highlight_score: float = 0.0

    @property
    def natural_duration_s(self) -> float:
        return self.out_sec - self.in_sec


class ControllingIdea(BaseModel):
    """The spine: the one decision the human authors. Substitutes for plot."""

    one_line: str  # value + cause, e.g. "the quiet exhaustion of earning a view"
    target_feeling: str  # e.g. pride | nostalgia | exhaustion | wonder ...
    authored_by: str = "human"  # human | human_from_ai_draft (provenance of the spine)


class Structure(BaseModel):
    type: str  # story_circle | kishotenketsu | string_of_pearls | theme_and_variations | ...
    target_arc_shape: str | None = None


class Beat(BaseModel):
    """One unit of the story: a dramatic FUNCTION the human/AI assigns, optionally filled
    with a chosen moment, plus ranked alternates and the rationale."""

    id: str
    order: int
    function: str  # dramatic function, e.g. establish_ordinary | low_point | return_changed
    intent: str = ""  # what this beat must make you feel
    target_duration_s: float  # pacing budget for this beat
    chosen_moment: str | None = None  # MomentId; None renders as a visible gap
    candidate_moment_ids: list[str] = Field(default_factory=list)
    transition_intent: str = "hard_cut"  # hard_cut | dissolve | match_cut | breath_pause
    why_moment: str = ""  # rationale that travels with the segment ("why this clip")


class Story(BaseModel):
    """The editable, branchable product. Human authors the controlling_idea; the AI fills
    beats. compile_story turns it into an IntentionList."""

    id: str
    name: str = ""
    controlling_idea: ControllingIdea
    structure: Structure
    target_duration_s: float = 0.0
    beats: list[Beat] = Field(default_factory=list)
    branch_of: str | None = None


class Segment(BaseModel):
    """One entry of the IntentionList. A 'clip' references a source range; a 'gap' is a
    deliberate hole (an unfilled beat) shown in the preview."""

    kind: str = "clip"  # clip | gap | photo (a still, held for duration_s)
    source_id: str | None = None  # source_clip_id for a clip; None for a gap
    in_sec: float | None = None
    out_sec: float | None = None
    duration_s: float
    motion: str = ""  # for a photo still: "" / "static" | "in" | "out" (Ken Burns at render)
    label: str = ""  # beat function, surfaced as a marker name / gap label
    note: str = ""  # rationale / provenance, surfaced as a marker note
    transition_in: str = "hard_cut"
    role: str = ""
    keywords: list[str] = Field(default_factory=list)
    favorite: bool = False
    enabled: bool = True  # disabled = kept in the doc but excluded from compile output
    duck: bool = False  # this clip's audio should duck the music (director duck_music flag)


class AudioHighlight(BaseModel):
    """A timeline window where a meaningful live sound (a child speaking, laughter) is
    foregrounded: the music bed dips and the clip's own audio rises, with short ramps at
    the edges. Times are TIMELINE seconds (same axis as the preview cursor / FCPXML offset).
    Detection authors these in source-clip seconds; a compile-time adapter projects them
    onto the timeline before they land here."""

    start_s: float
    end_s: float
    ramp_s: float = 0.40  # fade in AND out at the window edges (gentle default)
    music_duck_db: float | None = None  # None -> inherit MusicBed.music_duck_db
    clip_db: float | None = None  # None -> inherit MusicBed.highlight_db
    label: str = ""  # e.g. "child speaks"; rides into an FCP marker

    @model_validator(mode="after")
    def _check_window(self) -> AudioHighlight:
        if self.end_s <= self.start_s:
            raise ValueError(f"highlight end_s ({self.end_s}) must be > start_s ({self.start_s})")
        return self


class MusicBed(BaseModel):
    """A music track laid under the whole timeline. The memory reel plays music-first:
    the clips' own audio is ducked under the bed. Levels are in dBFS relative gain.

    Two music levels (gain_db steady, music_duck_db inside a highlight) and two clip levels
    (duck_db steady, highlight_db inside a highlight) implement the dynamic ducking."""

    path: str
    gain_db: float = 0.0  # the music level outside highlight windows
    duck_db: float = -15.0  # the clips' original audio outside highlight windows
    fade_out_s: float = 1.5  # music fade at the end of the timeline
    highlights: list[AudioHighlight] = Field(default_factory=list)
    music_duck_db: float = -12.0  # duck applied to the MUSIC bed DURING a window (gentle default)
    highlight_db: float = 0.0  # clip audio foregrounded DURING a window


class TrackFeatures(BaseModel):
    """A track's offline audio features, read directly at selection time. Computed once by
    `composerv music index` and cached in a sibling *.features.json sidecar (spec D2)."""

    path: str
    duration_s: float
    tempo_bpm: float = 0.0
    beat_times: list[float] = Field(default_factory=list)  # detect_beats output, reused
    mode: str = "unknown"  # "major" | "minor" | "unknown" (librosa approximation)
    energy_curve: list[float] = Field(default_factory=list)  # 16 points, 0..1 normalized
    valence: float = 0.5  # 0..1 brightness/positivity approximation
    source: str = ""  # provenance: library name / URL
    license: str = ""  # CC0 / CC-BY / user-owned ...
    # reserved for direction 3 (left empty this round):
    phrase_boundaries: list[float] = Field(default_factory=list)
    climax_t: float | None = None

    @model_validator(mode="after")
    def _check_energy_curve(self) -> TrackFeatures:
        if self.energy_curve and len(self.energy_curve) != 16:
            raise ValueError(
                f"energy_curve must be empty or exactly 16 floats, got {len(self.energy_curve)}"
            )
        for val in self.energy_curve:
            if val < 0.0 or val > 1.0:
                raise ValueError("energy_curve values must be in [0, 1]")
        return self


class MusicIntent(BaseModel):
    """The director's request: what KIND of music this reel wants. Emitted as part of the
    edit; the deterministic scorer (rank_tracks) finds the best-matching track afterward."""

    energy_curve: list[float] = Field(default_factory=list)  # 16 points the director wants
    tempo_lo: float = 0.0  # desired tempo band (0 = unconstrained)
    tempo_hi: float = 0.0
    mode_pref: str = "any"  # "major" | "minor" | "any"
    valence: float = 0.5
    target_duration_s: float = 0.0
    arc_text: str = ""  # preserves the director's prose arc, for audit

    @model_validator(mode="after")
    def _check_energy_curve(self) -> MusicIntent:
        if self.energy_curve and len(self.energy_curve) != 16:
            raise ValueError(
                f"energy_curve must be empty or exactly 16 floats, got {len(self.energy_curve)}"
            )
        for val in self.energy_curve:
            if val < 0.0 or val > 1.0:
                raise ValueError("energy_curve values must be in [0, 1]")
        return self


class IntentionList(BaseModel):
    """The ordered edit-decision list: the shared contract for preview and FCPXML."""

    story_id: str
    timeline_fps: int = 30
    segments: list[Segment] = Field(default_factory=list)
    music: MusicBed | None = None  # optional music bed; both preview and FCPXML honor it

    @property
    def total_duration_s(self) -> float:
        return sum(s.duration_s for s in self.segments if s.enabled)
