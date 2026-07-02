import re
from typing import cast
from llama_index.core import Document
from llama_index.core.node_parser import SentenceSplitter
from llama_index.core.schema import TextNode
from app.config import settings

# ── Marker grammar (line-start) ───────────────────────────────────────────
# UNIT markers — each begins a legal unit we want as its own chunk
ARTICLE_RE = re.compile(r"^\s*(?:ART(?:ICLE)?|Art(?:icle)?)\.?\s+(\d+(?:-[A-Z])?)\b")
SECTION_RE = re.compile(r"^\s*(?:SEC(?:TION)?|Sec(?:tion)?)\.?\s+(\d+(?:-[A-Z])?)\b")
# PARENT markers — context for structure_path; never a unit boundary themselves
RULE_RE = re.compile(r"^\s*(?:RULE|Rule)\s+(\d+)\b")
ARTICLE_ROMAN_RE = re.compile(r"^\s*ARTICLE\s+([IVXLCDM]+)\b")  # constitution divisions
PARENT_RE = re.compile(r"^\s*(?:BOOK|Book|TITLE|Title|CHAPTER|Chapter|SUBTITLE|Subtitle)\b.*")
_QUOTE = r'["\u201c\u2018\']'
QUOTED_ARTICLE_RE = re.compile(
	rf"^\s*{_QUOTE}\s*(?:ART(?:ICLE)?|Art(?:icle)?)\.?\s+(\d+(?:-[A-Z])?)\b"
)
QUOTED_SECTION_RE = re.compile(
	rf"^\s*{_QUOTE}\s*(?:SEC(?:TION)?|Sec(?:tion)?)\.?\s+(\d+(?:-[A-Z])?)\b"
)
PARTIAL_ELLIPSIS_RE = re.compile(r"(?:^|\s)x\s+x\s+x(?:\s|$)", re.IGNORECASE)

MIN_UNITS = 5  # auto-detect: minimum length of a monotonic ascending run to call a doc structural

# ── Enumeration markers (line-start, scanned only inside an oversized unit) ──
_PAREN   = re.compile(r"^\s*\(([a-z]{1,4}|\d{1,3})\)\s")   # (a) (iv) (aa) (12) — {1,4} catches (iii)/(viii)
_DECIMAL = re.compile(r"^\s*(\d{1,3}(?:\.\d{1,3})+)\s")     # 4.2  123.1 — own series, never split(".")[0]

_RVALS = [(1000, "m"), (900, "cm"), (500, "d"), (400, "cd"), (100, "c"),
          (90, "xc"), (50, "l"), (40, "xl"), (10, "x"), (9, "ix"), (5, "v"), (4, "iv"), (1, "i")]
_RMAP = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100, "d": 500, "m": 1000}


def _to_roman(n: int) -> str:
	out = ""
	for v, sym in _RVALS:
		while n >= v:
			out += sym
			n -= v
	return out


def _from_roman(s: str) -> int:
	total = prev = 0
	for ch in reversed(s):
		v = _RMAP[ch]
		total += -v if v < prev else v
		prev = v
	return total


def _alpha_succ(s: str) -> str:
	"""Legal homogeneous run: a→b … z→aa→bb, zz→aaa (NOT spreadsheet aa→ab)."""
	ch = s[0]
	return chr(ord(ch) + 1) * len(s) if ch != "z" else "a" * (len(s) + 1)


def _succ(kind: str, last: str) -> str:
	if kind == "arabic":
		return str(int(last) + 1)
	if kind == "alpha":
		return _alpha_succ(last)
	if kind == "roman":
		return _to_roman(_from_roman(last) + 1)
	if kind == "decimal":  # 4.2→4.3 ; 123.1→123.2 ; keeps the prefix
		head, _, tail = last.rpartition(".")
		return f"{head}.{int(tail) + 1}"
	return ""


def _open_kind(body: str) -> str:
	if body.isdigit():
		return "arabic"
	if body == "i":  # ONLY (i) may OPEN a roman series; multi-char romans only continue
		return "roman"
	return "alpha"


def chunk_texts(text: str, source_metadata: dict) -> list[TextNode]:
	if source_metadata.get("amends"):
		return _amendment_nodes(text, source_metadata)

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


def _amendment_units(text: str) -> list[dict]:
	"""Collect both an amendment law's own units and inserted provisions.

	Quoted markers are always insertions. Unquoted markers can ALSO be insertions when
	extraction strips the quotation marks (e.g. lawphil's RA 11576/RA 10707 pages): the
	surviving signal is SEQUENCE — an amendment's own sections run 1, 2, 3, …, while a
	quote-stripped insertion breaks the series (Section 1, Section 19, Section 2, …).
	Unquoted Article markers are always insertions: an RA/PD amendment's own units are
	Sections, never Articles."""
	units: list[dict] = []
	parents: dict[str, str] = {}
	offset = 0
	own_next = 1  # the amendment's own Section series: 1, 2, 3, …
	for line in text.splitlines(keepends=True):
		if RULE_RE.match(line):
			parents["rule"] = line.strip()
		elif ARTICLE_ROMAN_RE.match(line):
			parents["division"] = line.strip()
		elif PARENT_RE.match(line):
			parents["parent"] = line.strip()
		elif (m := QUOTED_ARTICLE_RE.match(line)):
			units.append({**_unit(offset, "article", m.group(1), f"Article {m.group(1)}", parents), "inserted": True})
		elif (m := QUOTED_SECTION_RE.match(line)):
			units.append({**_unit(offset, "section", m.group(1), f"Section {m.group(1)}", parents), "inserted": True})
		elif (m := ARTICLE_RE.match(line)):
			units.append({**_unit(offset, "article", m.group(1), f"Article {m.group(1)}", parents), "inserted": True})
		elif (m := SECTION_RE.match(line)):
			num = m.group(1)
			is_own = num.isdigit() and int(num) == own_next
			if is_own:
				own_next += 1
			units.append({**_unit(offset, "section", num, f"Section {num}", parents), "inserted": not is_own})
		offset += len(line)

	for i, u in enumerate(units):
		u["end"] = units[i + 1]["start"] if i + 1 < len(units) else len(text)
	return units


def _amendment_target(sm: dict) -> str:
	amends = sm.get("amends") or []
	if len(amends) == 1:
		return amends[0]
	if sm.get("amends_namespace"):
		return sm["amends_namespace"]
	target = sm.get("source_id")
	print(f"[WARN] {target}: multi-target amendment without amends_namespace; using own namespace")
	return target


def _slug(s: str) -> str:
	return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def _provision_id(source_id: str | None, unit_type: str, unit_number: str, structure_path: str) -> str | None:
	"""Canonical, stable join key for a legal provision (e.g. 'revised_penal_code:article:335').
	Uses the real source_id as prefix (no vanity-name table). Articles are globally unique within
	a source, so they need no path; section/rule numbers RESET per parent, so the structure_path is
	folded in to disambiguate — mirroring _parent_key/_locator. Oversized units split into
	enumeration leaves inherit the PARENT unit's provision_id (set once on the base dict), so an
	override on the article hides all its leaves."""
	if not source_id:
		return None
	if unit_type in ("section", "rule") and structure_path:
		return f"{source_id}:{_slug(structure_path)}:{unit_type}:{unit_number}".lower()
	return f"{source_id}:{unit_type}:{unit_number}".lower()


def _number_key(number: str) -> tuple[int, str]:
	head, _, suffix = number.partition("-")
	return (int(head), suffix)


def _looks_structural(units: list[dict]) -> bool:
	"""Cautious auto-detect: need a long ASCENDING run, not just many markers.
	Defeats prose decisions that quote scattered sections (e.g. 'Sec. 16' then 'Sec. 15')."""
	if len(units) < MIN_UNITS:
		return False
	nums = [_number_key(u["number"]) for u in units]
	longest = run = 1
	for a, b in zip(nums, nums[1:]):
		run = run + 1 if b > a else 1
		longest = max(longest, run)
	return longest >= MIN_UNITS


def _amendment_nodes(text: str, sm: dict) -> list[TextNode]:
	units = _amendment_units(text)
	if not units:
		return _prose_nodes(text, sm)

	nodes: list[TextNode] = []
	preamble = text[: units[0]["start"]].strip()
	if preamble:
		nodes += _prose_nodes(preamble, sm)

	target = _amendment_target(sm)
	for u in units:
		seg = text[u["start"]: u["end"]].strip()
		if not seg:
			continue
		if u.get("inserted"):
			provision_id = f"{target}:{u['type']}:{u['number']}".lower()
			base = {
				**sm,
				"is_structural": True,
				"unit_type": u["type"],
				"unit_number": u["number"],
				"unit_label": u["label"],
				"structure_path": u["path"],
				"provision_id": provision_id,
				"inserted_into": target,
			}
			if PARTIAL_ELLIPSIS_RE.search(seg):
				base["provision_partial"] = True
		else:
			base = {
				**sm,
				"is_structural": True,
				"unit_type": u["type"],
				"unit_number": u["number"],
				"unit_label": u["label"],
				"structure_path": u["path"],
				"provision_id": _provision_id(sm.get("source_id"), u["type"], u["number"], u["path"]),
			}
		if len(seg) <= settings.chunk_size * 4:
			nodes.append(TextNode(text=_with_source_context(seg, base), metadata=base))
		else:
			nodes += _enumeration_nodes(seg, u, base)
	return nodes


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
			"provision_id": _provision_id(sm.get("source_id"), u["type"], u["number"], u["path"]),
		}
		if len(seg) <= settings.chunk_size * 4:  # ~4 chars/token; whole unit fits
			nodes.append(TextNode(text=_with_source_context(seg, base), metadata=base))
		else:  # oversized unit → provision-aware sub-split, keep identity
			nodes += _enumeration_nodes(seg, u, base)
	return nodes


def _scan_marker(line: str):
	if (m := _PAREN.match(line)):
		return m.group(1), False
	if (m := _DECIMAL.match(line)):
		return m.group(1), True
	return None, False


def _resolve(stack: list[dict], body: str, is_dec: bool):
	"""Mutate stack for this line-start marker. Returns (kind, parent_kind).
	Classifies by SEQUENCE/CONTEXT, never by glyph shape."""
	# 1. try to CONTINUE the deepest matching frame, popping anything deeper
	for depth in range(len(stack) - 1, -1, -1):
		fr = stack[depth]
		if is_dec != (fr["kind"] == "decimal"):  # decimal markers continue only decimal frames, and vice-versa
			continue
		if body == _succ(fr["kind"], fr["last"]):
			del stack[depth + 1:]
			fr["last"] = body
			return fr["kind"], (stack[depth - 1]["kind"] if depth > 0 else None)
	# 2. else OPEN a new nested frame
	kind = "decimal" if is_dec else _open_kind(body)
	parent_kind = stack[-1]["kind"] if stack else None
	stack.append({"kind": kind, "last": body})
	return kind, parent_kind


def _render(stack: list[dict], u: dict):
	"""Build (unit_number, label) from non-roman frames. enum_path = concat of (marker)s."""
	non_roman = [f for f in stack if f["kind"] != "roman"]
	cap = u["type"].capitalize()
	if non_roman and non_roman[-1]["kind"] == "decimal":
		token = non_roman[-1]["last"]  # "4.2" already encodes the section number
		return token, f"{cap} {token}"
	enum_path = "".join(f"({f['last']})" for f in non_roman)
	unit_number = f"{u['number']}{enum_path}"  # "4" + "(c)(4)" → "4(c)(4)"
	return unit_number, f"{cap} {unit_number}"


def _sized_nodes(text: str, meta: dict) -> list[TextNode]:
	"""One node if it fits; else size-split with part_index. Universal leaf for this module."""
	if len(text) <= settings.chunk_size * 4:
		return [TextNode(text=_with_source_context(text, meta), metadata=meta)]
	return [TextNode(text=_with_source_context(sub, meta), metadata={**meta, "part_index": i})
			for i, sub in enumerate(_splitter().split_text(text))]


def _enum_boundaries(seg: str, u: dict) -> list[tuple[int, str, str]]:
	"""Provision boundaries inside an oversized unit: (char_offset, unit_number, label).
	Split a marker iff kind != roman AND parent_kind != roman (roman series & anything
	under a roman parent stay glued). Classifies by SEQUENCE/CONTEXT, never by glyph shape."""
	stack: list[dict] = []
	boundaries: list[tuple[int, str, str]] = []
	pos = 0
	for line in seg.splitlines(keepends=True):
		body, is_dec = _scan_marker(line)
		if body is not None:
			kind, parent_kind = _resolve(stack, body, is_dec)
			if kind != "roman" and parent_kind != "roman":
				unit_number, label = _render(stack, u)
				boundaries.append((pos, unit_number, label))
		pos += len(line)
	return boundaries


def _parent_key(meta: dict) -> str:
	"""Stable id for a parent section — source_id + path disambiguates repeated section
	numbers (e.g. Article III Section 1 vs Article VI Section 1 in the Constitution)."""
	return "::".join([
		meta.get("source_id") or "",
		meta.get("structure_path") or "",
		meta.get("unit_label") or "",
	])


def _enumeration_nodes(seg: str, u: dict, base: dict) -> list[TextNode]:
	"""Provision-aware sub-split of an oversized legal unit into fine leaves. Each leaf
	carries parent_key so post-rerank parent expansion can merge them back to the whole
	section when enough siblings survive the cutoff."""
	boundaries = _enum_boundaries(seg, u)

	if not boundaries:  # no provisions found → behave like the old size-split (no parent_key)
		return _sized_nodes(seg, base)

	pk = _parent_key(base)
	nodes: list[TextNode] = []
	head = seg[: boundaries[0][0]].strip()  # chapeau before first provision = the parent item text
	if head:
		nodes += _sized_nodes(head, {**base, "parent_key": pk})

	bounds = boundaries + [(len(seg), "", "")]
	path = base.get("structure_path") or ""
	for (start, unit_number, label), (end, _, _) in zip(bounds, bounds[1:]):
		body_text = seg[start:end].strip()
		if not body_text:
			continue
		crumb = f"{path} — {label}" if path else label  # breadcrumb baked INTO the text → self-contained
		meta = {**base, "unit_number": unit_number, "unit_label": label, "parent_key": pk}
		nodes += _sized_nodes(f"{crumb}\n{body_text}", meta)
	return nodes


def extract_parents(text: str, source_metadata: dict) -> list[dict]:
	"""Whole-section text for the oversized, enumeration-split units that produce
	parent_key'd leaves — the merge target for post-rerank parent expansion. Only units
	that actually fragment get a parent row; size-gated and prose docs return nothing."""
	if source_metadata.get("amends"):
		units = _amendment_units(text)
		if not units:
			return []
		return _parent_rows_for_units(text, units, source_metadata)

	hint = source_metadata.get("structure", "auto")
	if hint == "prose":
		return []
	units = _detect_units(text)
	if not units or (hint != "hierarchical" and not _looks_structural(units)):
		return []

	return _parent_rows_for_units(text, units, source_metadata)


def _parent_rows_for_units(text: str, units: list[dict], source_metadata: dict) -> list[dict]:
	parents: list[dict] = []
	for u in units:
		seg = text[u["start"]: u["end"]].strip()
		if len(seg) <= settings.chunk_size * 4:        # fits whole → not split, no parent needed
			continue
		if not _enum_boundaries(seg, u):                # oversized but no enumeration → size-split, no parent_key
			continue
		meta = {**source_metadata, "unit_label": u["label"], "structure_path": u["path"]}
		parents.append({
			"parent_key": _parent_key(meta),
			"source_id": source_metadata.get("source_id"),
			"title": source_metadata.get("title"),
			"url": source_metadata.get("url"),
			"unit_type": u["type"],
			"unit_label": u["label"],
			"structure_path": u["path"],
			"text": seg,
			"char_count": len(seg),
		})
	return parents


def _prose_nodes(text: str, sm: dict) -> list[TextNode]:
	doc = Document(text=text, metadata={**sm, "is_structural": False})
	nodes = cast(list[TextNode], _splitter().get_nodes_from_documents([doc]))
	for node in nodes:
		node.text = _with_source_context(node.text, node.metadata)
	return nodes


def _splitter() -> SentenceSplitter:
	return SentenceSplitter(chunk_size=settings.chunk_size, chunk_overlap=settings.chunk_overlap)


def _with_source_context(text: str, metadata: dict) -> str:
	if not text.strip():
		return text

	title = metadata.get("title")
	official_number = metadata.get("official_number")
	if not title and not official_number:
		return text

	header = f"Source: {title}" if title else "Source"
	if official_number:
		header += f" ({official_number})"
	return f"{header}\n{text}"
