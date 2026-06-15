from __future__ import annotations

EXTRACT_HEADINGS_SCRIPT = r"""
() => {
  const cleanText = (v) => (v || "").replace(/\s+/g, " ").trim();

  const getImageContext = (el) => {
    const img = el.querySelector("img");
    if (img) return { hasImage: true, imageType: "img", imageAlt: (img.getAttribute("alt") || "").trim() };

    const svg = el.querySelector("svg");
    if (svg) {
      const t = svg.querySelector("title");
      return { hasImage: true, imageType: "svg", imageAlt: t ? cleanText(t.textContent) : (svg.getAttribute("aria-label") || "").trim() };
    }

    const roleImg = el.querySelector('[role="img"]');
    if (roleImg) return { hasImage: true, imageType: "role-img", imageAlt: (roleImg.getAttribute("aria-label") || roleImg.getAttribute("title") || "").trim() };

    const ariaLabel = (el.getAttribute("aria-label") || "").trim();
    if (ariaLabel) return { hasImage: true, imageType: "aria-label", imageAlt: ariaLabel };

    const labelledBy = el.getAttribute("aria-labelledby");
    if (labelledBy) {
      const labelText = labelledBy.split(/\s+/)
        .map(id => document.getElementById(id))
        .filter(Boolean)
        .map(ref => cleanText(ref.textContent))
        .join(" ").trim();
      if (labelText) return { hasImage: true, imageType: "aria-labelledby", imageAlt: labelText };
    }

    for (const e of [el, ...el.querySelectorAll("*")]) {
      const bg = window.getComputedStyle(e).backgroundImage || "";
      if (bg.includes("url(") && !bg.includes("gradient"))
        return { hasImage: true, imageType: "background-image", imageAlt: (e.getAttribute("aria-label") || e.getAttribute("title") || "").trim() };
    }

    return { hasImage: false, imageType: null, imageAlt: "" };
  };

  const isVisible = (el) => {
    if (el.hidden || el.getAttribute("aria-hidden") === "true") return false;
    if (el.closest("[hidden], [aria-hidden='true']")) return false;
    const s = window.getComputedStyle(el);
    if (s.display === "none" || s.visibility === "hidden" || parseFloat(s.opacity || "1") === 0) return false;
    const r = el.getBoundingClientRect();
    return (r.width > 0 && r.height > 0) || el.offsetParent !== null;
  };

  const isIgnored = (el) => {
    if (el.closest('dialog, [role="dialog"], [role="alertdialog"], [aria-modal="true"], .modal, .popup')) return true;
    const cookieSel = '[class*="cookie" i], [id*="cookie" i], [class*="consent" i], [id*="consent" i], [class*="gdpr" i], [id*="gdpr" i], [class*="cc-" i], [id*="cc-" i]';
    const cc = el.closest(cookieSel);
    if (cc) {
      const pos = window.getComputedStyle(cc).position;
      if (pos === "fixed" || pos === "absolute") return true;
      if (cc.closest('.modal, .popup, [class*="modal" i], [class*="popup" i]')) return true;
    }
    return false;
  };

  const items = [];
  for (const el of document.querySelectorAll("h1, h2, h3, h4, h5, h6")) {
    if (isIgnored(el)) continue;
    const imgCtx = getImageContext(el);
    items.push({
      tag: el.tagName.toLowerCase(),
      level: parseInt(el.tagName[1], 10),
      text: cleanText(el.innerText || el.textContent),
      hasImage: imgCtx.hasImage,
      imageType: imgCtx.imageType,
      imageAlt: imgCtx.imageAlt,
      visible: isVisible(el),
    });
  }

  return { title: document.title || "", url: window.location.href, headings: items };
}
"""

def extract_headings(page) -> dict:
    return page.evaluate(EXTRACT_HEADINGS_SCRIPT)