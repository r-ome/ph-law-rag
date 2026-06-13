import re
from typing import cast
from llama_index.core import Document
from llama_index.core.node_parser import SentenceSplitter
from llama_index.core.schema import TextNode
from app.config import settings

# ── Marker grammar (line-start) ───────────────────────────────────────────
# UNIT markers — each begins a legal unit we want as its own chunk
ARTICLE_RE = re.compile(r"^\s*(?:ART(?:ICLE)?|Art(?:icle)?)\.?\s+(\d+)\b")
SECTION_RE = re.compile(r"^\s*(?:SEC(?:TION)?|Sec(?:tion)?)\.?\s+(\d+)\b")
# PARENT markers — context for structure_path; never a unit boundary themselves
RULE_RE = re.compile(r"^\s*(?:RULE|Rule)\s+(\d+)\b")
ARTICLE_ROMAN_RE = re.compile(r"^\s*ARTICLE\s+([IVXLCDM]+)\b")  # constitution divisions
PARENT_RE = re.compile(r"^\s*(?:BOOK|Book|TITLE|Title|CHAPTER|Chapter|SUBTITLE|Subtitle)\b.*")

MIN_UNITS = 5  # auto-detect: minimum length of a monotonic ascending run to call a doc structural


def chunk_texts(text: str, source_metadata: dict) -> list[TextNode]:
	# Rule 1: the manual hint wins.
	hint = source_metadata.get("structure", "auto")
	if hint == "prose":
		return _prose_nodes(text, source_metadata)

	units = _detect_units(text)

	if hint == "hierarchical":
		# Trust the hint, but fall back if parsing genuinely found nothing.
		return _structural_nodes(text, units, source_metadata) if units \
			else _prose_nodes(text, source_metadata)

	# hint == "auto": only go structural if the units look real (cautious).
	return _structural_nodes(text, units, source_metadata) if _looks_structural(units) \
		else _prose_nodes(text, source_metadata)


def _detect_units(text: str) -> list[dict]:
	"""One pass: collect Article/Section UNITS, tracking parent context for structure_path."""
	units: list[dict] = []
	parents: dict[str, str] = {}  # latest division / rule / book-title-chapter
	offset = 0
	for line in text.splitlines(keepends=True):
		if RULE_RE.match(line):
			parents["rule"] = line.strip()
		elif ARTICLE_ROMAN_RE.match(line):
			parents["division"] = line.strip()
		elif PARENT_RE.match(line):
			parents["parent"] = line.strip()
		elif (m := ARTICLE_RE.match(line)):
			units.append(_unit(offset, "article", m.group(1), f"Article {m.group(1)}", parents))
		elif (m := SECTION_RE.match(line)):
			units.append(_unit(offset, "section", m.group(1), f"Section {m.group(1)}", parents))
		offset += len(line)

	for i, u in enumerate(units):  # each unit runs until the next one (or EOF)
		u["end"] = units[i + 1]["start"] if i + 1 < len(units) else len(text)
	return units


def _unit(start: int, utype: str, number: str, label: str, parents: dict) -> dict:
	path = " > ".join(
		p for p in (parents.get("division"), parents.get("rule"), parents.get("parent")) if p
	)
	return {"start": start, "type": utype, "number": number, "label": label, "path": path}


def _looks_structural(units: list[dict]) -> bool:
	"""Cautious auto-detect: need a long ASCENDING run, not just many markers.
	Defeats prose decisions that quote scattered sections (e.g. 'Sec. 16' then 'Sec. 15')."""
	if len(units) < MIN_UNITS:
		return False
	nums = [int(u["number"]) for u in units]
	longest = run = 1
	for a, b in zip(nums, nums[1:]):
		run = run + 1 if b > a else 1
		longest = max(longest, run)
	return longest >= MIN_UNITS


def _structural_nodes(text: str, units: list[dict], sm: dict) -> list[TextNode]:
	nodes: list[TextNode] = []

	# Preamble before the first unit (statute title / enacting clause) → prose, nothing lost.
	preamble = text[: units[0]["start"]].strip()
	if preamble:
		nodes += _prose_nodes(preamble, sm)

	for u in units:
		seg = text[u["start"]: u["end"]].strip()
		if not seg:
			continue
		base = {
			**sm,
			"is_structural": True,
			"unit_type": u["type"],
			"unit_number": u["number"],
			"unit_label": u["label"],
			"structure_path": u["path"],
		}
		if len(seg) <= settings.chunk_size * 4:  # ~4 chars/token; whole unit fits
			nodes.append(TextNode(text=seg, metadata=base))
		else:  # oversized unit → sub-split, keep identity
			for i, sub in enumerate(_splitter().split_text(seg)):
				nodes.append(TextNode(text=sub, metadata={**base, "part_index": i}))
	return nodes


def _prose_nodes(text: str, sm: dict) -> list[TextNode]:
	doc = Document(text=text, metadata={**sm, "is_structural": False})
	return cast(list[TextNode], _splitter().get_nodes_from_documents([doc]))


def _splitter() -> SentenceSplitter:
	return SentenceSplitter(chunk_size=settings.chunk_size, chunk_overlap=settings.chunk_overlap)
