from __future__ import annotations


EXTRACT_HEADINGS_SCRIPT = """
() => {
  const tags = ["H1", "H2", "H3", "H4", "H5", "H6"];
  const items = [];

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

    items.push({
      tag: element.tagName.toLowerCase(),
      level: tags.indexOf(element.tagName) + 1,
      text: (element.innerText || element.textContent || "").replace(/\\s+/g, " ").trim(),
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