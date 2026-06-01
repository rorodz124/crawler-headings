from __future__ import annotations


EXTRACT_HEADINGS_SCRIPT = """
() => {
  const cleanText = (value) => (value || "").replace(/\s+/g, " ").trim();

  // Um heading tem conteúdo se tiver texto visível OU contiver uma imagem
  // (independentemente de ter alt ou não).
  const hasContent = (element) => {
    const text = cleanText(element.innerText || element.textContent);
    if (text) return true;
    if (element.querySelector("img")) return true;
    return false;
  };

  // Visibilidade: escondido por atributo ou por estilo computado.
  // Não usamos getBoundingClientRect() como condição eliminatória porque
  // elementos abaixo do fold ou em carrosséis inativos têm rect zero mas
  // são reais. offsetParent !== null indica que o elemento está no fluxo
  // de layout normal (cobre esses casos).
  const isVisible = (element) => {
    const hiddenByAttr =
      element.hidden ||
      element.getAttribute("aria-hidden") === "true" ||
      !!element.closest("[hidden], [aria-hidden='true']");
    if (hiddenByAttr) return false;

    const style = window.getComputedStyle(element);
    const hiddenByStyle =
      style.display === "none" ||
      style.visibility === "hidden" ||
      Number(style.opacity || "1") === 0;
    if (hiddenByStyle) return false;

    // Está no fluxo de layout?
    const rect = element.getBoundingClientRect();
    const rectOk = rect.width > 0 && rect.height > 0;
    const inFlow = element.offsetParent !== null;
    return rectOk || inFlow;
  };

  // Verifica se o elemento está dentro de um modal, diálogo ou banner de consentimento/cookies.
  const isIgnoredContainer = (element) => {
    // Ignorar headings dentro de modais, diálogos e popups comuns
    if (element.closest('dialog, [role="dialog"], [role="alertdialog"], [aria-modal="true"], .modal, .popup')) {
      return true;
    }

    // Ignorar banners de cookies/consentimento que sejam flutuantes ou modais
    const cookieContainer = element.closest('[class*="cookie" i], [id*="cookie" i], [class*="consent" i], [id*="consent" i], [class*="gdpr" i], [id*="gdpr" i]');
    if (cookieContainer) {
      const style = window.getComputedStyle(cookieContainer);
      if (style.position === 'fixed' || style.position === 'absolute') {
        return true;
      }
      if (cookieContainer.closest('.modal, .popup, [class*="modal" i], [class*="popup" i]')) {
        return true;
      }
    }
    return false;
  };

  const items = [];
  for (const el of document.querySelectorAll("h1, h2, h3, h4, h5, h6")) {
    if (isIgnoredContainer(el)) {
      continue;
    }
    const level = parseInt(el.tagName[1], 10);
    const text = cleanText(el.innerText || el.textContent);
    const hasImage = el.querySelector("img") !== null;

    items.push({
      tag:      el.tagName.toLowerCase(),
      level:    level,
      text:     text,
      hasImage: hasImage,
      visible:  isVisible(el),
    });
  }

  return {
    title:    document.title || "",
    url:      window.location.href,
    headings: items,
  };
}
"""


def extract_headings(page) -> dict:
    return page.evaluate(EXTRACT_HEADINGS_SCRIPT)