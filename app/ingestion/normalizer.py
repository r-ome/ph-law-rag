import re

def normalize_text(text: str) -> str:
	lines = text.splitlines()
	normalized_lines = []
	previous_blank = False

	for line in lines:
		cleaned = re.sub(r"\s", " ", line).strip()
		if cleaned == "":
			if previous_blank:
				continue
			previous_blank = True
			normalized_lines.append("")
			continue

		previous_blank = False
		normalized_lines.append(cleaned)

	result = "\n".join(normalized_lines).strip()
	return result
