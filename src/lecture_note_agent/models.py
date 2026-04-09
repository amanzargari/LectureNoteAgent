from __future__ import annotations

from typing import List
from pydantic import BaseModel, Field


class SlideUnit(BaseModel):
    slide_number: int
    title: str = ""
    text: str = ""
    image_refs: List[str] = Field(default_factory=list)
    formula_candidates: List[str] = Field(default_factory=list)


class TranscriptSegment(BaseModel):
    segment_id: str
    timestamp: str = ""
    speaker: str = "Teacher"
    text: str


class SourceBundle(BaseModel):
    course_name: str
    slides: List[SlideUnit] = Field(default_factory=list)
    transcript: List[TranscriptSegment] = Field(default_factory=list)


class GenerationArtifacts(BaseModel):
    checklist_markdown: str
    draft_markdown: str
    final_markdown: str
    audit_json: str
    model_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
