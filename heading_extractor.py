from __future__ import annotations


EXTRACT_HEADINGS_SCRIPT = """
() => {
  const tags = ["H1", "H2", "H3", "H4", "H5", "H6"];
  const items = [];

  const cleanText = (value) => (value || "").replace(/\\s+/g, " ").trim();

  const imageText = (image) => cleanText([
    image.getAttribute("alt"),
    image.getAttribute("aria-label"),
    image.getAttribute("title")
  ].filter(Boolean).join(" "));

  for (const element of document.querySelectorAll("h1, h2, h3, h4, h5, h6")) {
    const style = window.getComputedStyle(element);
    const rect = element.getBoundingClientRect();
    const hiddenByAttr =
      element.hidden ||
      element.getAttribute("aria-hidden") === "true" ||
      !!element.closest("[hidden], [aria-hidden='true']");

    const hiddenByStyle =
      style.display === "none" ||
      style.visibility === "hidden" ||
      Number(style.opacity || "1") === 0;

    const visible = !hiddenByAttr && !hiddenByStyle && rect.width > 0 && rect.height > 0;
    const text = cleanText(element.innerText || element.textContent);
    const ariaLabel = cleanText(element.getAttribute("aria-label"));
    const title = cleanText(element.getAttribute("title"));
    const imageTexts = Array.from(element.querySelectorAll("img"))
      .map(imageText)
      .filter(Boolean);
    const accessibleText = cleanText([text, ariaLabel, title, ...imageTexts].join(" "));

    items.push({
      tag: element.tagName.toLowerCase(),
      level: tags.indexOf(element.tagName) + 1,
      text,
      accessibleText,
      imageTexts,
      hasImage: imageTexts.length > 0 || element.querySelectorAll("img").length > 0,
      html: element.innerHTML,
      visible,
      hiddenByAttr,
      hiddenByStyle,
    });
  }

  return {
    title: document.title || "",
    url: window.location.href,
    headings: items,
  };
}
"""


def extract_headings(page) -> dict:
    return page.evaluate(EXTRACT_HEADINGS_SCRIPT)