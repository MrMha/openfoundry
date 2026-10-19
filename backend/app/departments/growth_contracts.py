"""Structured draft assets and deterministic, escaped Mermaid architecture output."""

from __future__ import annotations

import html
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class DiagramNode(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
    label: str = Field(min_length=1, max_length=160, pattern=r"\S")


class DiagramEdge(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: str
    target: str
    label: str | None = Field(default=None, max_length=160)


class ArchitectureDiagram(BaseModel):
    model_config = ConfigDict(extra="forbid")
    nodes: list[DiagramNode] = Field(min_length=1, max_length=32)
    edges: list[DiagramEdge] = Field(max_length=64)

    @model_validator(mode="after")
    def valid_graph(self) -> ArchitectureDiagram:
        keys = {node.key for node in self.nodes}
        if len(keys) != len(self.nodes):
            raise ValueError("Architecture node keys must be unique")
        if any(edge.source not in keys or edge.target not in keys for edge in self.edges):
            raise ValueError("Architecture edges must reference declared nodes")
        return self

    @staticmethod
    def _label(value: str) -> str:
        return html.escape(" ".join(value.split()), quote=True).replace("|", "&#124;")

    def mermaid(self) -> str:
        lines = ["flowchart TD"]
        lines.extend(f'    n_{node.key}["{self._label(node.label)}"]' for node in self.nodes)
        for edge in self.edges:
            arrow = f'-->|"{self._label(edge.label)}"|' if edge.label else "-->"
            lines.append(f"    n_{edge.source} {arrow} n_{edge.target}")
        return "\n".join(lines) + "\n"


class GrowthAsset(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    kind: Literal[
        "show_hn", "reddit", "x", "career_profile", "cover_letter", "outreach", "portfolio"
    ]
    title: str = Field(min_length=1, max_length=160, pattern=r"\S")
    content: str = Field(min_length=1, max_length=20_000, pattern=r"\S")

    @field_validator("key")
    @classmethod
    def reserved_names(cls, key: str) -> str:
        if key in {"readme", "sources", "manifest", "architecture"}:
            raise ValueError("Asset key collides with package metadata")
        return key


class GrowthResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["launch", "career"]
    summary: str = Field(min_length=1, max_length=8000, pattern=r"\S")
    readme: str = Field(min_length=1, max_length=64_000, pattern=r"\S")
    diagram: ArchitectureDiagram | None
    assets: list[GrowthAsset] = Field(min_length=1, max_length=12)

    @model_validator(mode="after")
    def complete_package(self) -> GrowthResult:
        if len({asset.key for asset in self.assets}) != len(self.assets):
            raise ValueError("Asset keys must be unique")
        kinds = {asset.kind for asset in self.assets}
        launch_kinds = {"show_hn", "reddit", "x"}
        if self.mode == "launch" and (self.diagram is None or not launch_kinds <= kinds):
            raise ValueError("Launch packages require architecture and Show HN/Reddit/X drafts")
        if self.mode == "career" and not kinds.intersection(
            {"career_profile", "cover_letter", "outreach", "portfolio"}
        ):
            raise ValueError("Career packages require a tailored career or outreach asset")
        if "```mermaid" in self.readme.casefold():
            raise ValueError("Architecture diagrams are rendered from the structured graph")
        return self
