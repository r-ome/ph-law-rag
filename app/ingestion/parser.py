from bs4 import BeautifulSoup
from io import BytesIO
import trafilatura
import pdfplumber

def parse_html(content: bytes, url: str, extractor: str = "auto") -> str:
	# extractor="bs4": manifest override for pages where trafilatura's main-content
	# detection silently drops law text (e.g. RA 8353's enacting sections).
	if extractor != "bs4":
		extracted = trafilatura.extract(
			content.decode("utf-8", errors="ignore"),
			url=url,
			include_comments=False,
			include_tables=True,
		)
		if extracted and extracted.strip():
			return extracted.strip()

	soup = BeautifulSoup(content, "html.parser")
	return soup.get_text(separator="\n", strip=True)

def parse_pdf(content: bytes) -> str:
	parts: list[str] = []

	with pdfplumber.open(BytesIO(content)) as pdf:
		for page in pdf.pages:
			text = page.extract_text()
			if not text:
				continue
			parts.append(text.strip())

	return "\n".join(part for part in parts if part)
