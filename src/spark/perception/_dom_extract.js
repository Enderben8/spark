// Injected into each frame by perception/dom.py via frame.evaluate().
// Returns one JSON-serialisable object per frame: text blocks (paragraph-ish
// granularity, for the readable-text merge), the interactive-element
// inventory, a canvas/img "media dominance" ratio (drives the OCR
// escalation decision in BUILD_SPEC §6.4/§6.5), and basic page geometry.
//
// Deliberately implemented as one self-contained evaluate() call rather than
// round-tripping individual ElementHandles — much faster, and the whole
// point is a single consistent snapshot of one frame at one instant.
(() => {
  function isVisible(el) {
    if (!el || el.nodeType !== 1) return false;
    const style = getComputedStyle(el);
    if (style.display === "none" || style.visibility === "hidden") return false;
    if (parseFloat(style.opacity) === 0) return false;
    if (el.getAttribute("aria-hidden") === "true") return false;
    const rect = el.getBoundingClientRect();
    if (rect.width <= 0 && rect.height <= 0) return false;
    return true;
  }

  const SKIP_TAGS = new Set(["SCRIPT", "STYLE", "NOSCRIPT", "TEMPLATE"]);
  const BLOCK_DISPLAYS = new Set([
    "block", "list-item", "table", "table-row", "table-cell",
    "table-row-group", "flex", "grid", "flow-root",
  ]);

  function toBBox(rect) {
    return {
      x: Math.round(rect.x + window.scrollX),
      y: Math.round(rect.y + window.scrollY),
      w: Math.round(rect.width),
      h: Math.round(rect.height),
    };
  }

  // -- readable text, at roughly-paragraph granularity ---------------------

  function collectTextBlocks(root) {
    const blocks = [];

    function isLeafBlock(el) {
      for (const child of el.children) {
        if (SKIP_TAGS.has(child.tagName)) continue;
        const d = getComputedStyle(child).display;
        if (BLOCK_DISPLAYS.has(d) || child.tagName === "BR") return false;
      }
      return true;
    }

    function walk(el) {
      if (!el || el.nodeType !== 1 || SKIP_TAGS.has(el.tagName)) return;
      if (!isVisible(el)) return;
      if (isLeafBlock(el)) {
        const text = (el.innerText || "").trim();
        if (text) {
          blocks.push({
            text,
            tag: el.tagName.toLowerCase(),
            bbox: toBBox(el.getBoundingClientRect()),
          });
        }
        return;
      }
      for (const child of Array.from(el.children)) walk(child);
    }

    walk(root);
    return blocks;
  }

  // -- canvas/img "how much of this page is pixels, not markup" -----------

  function computeMediaDominance() {
    let mediaArea = 0;
    const totalArea = Math.max(
      document.documentElement.scrollWidth * document.documentElement.scrollHeight,
      1
    );
    document.querySelectorAll("canvas, img, embed, object").forEach((el) => {
      if (!isVisible(el)) return;
      const r = el.getBoundingClientRect();
      mediaArea += Math.max(r.width, 0) * Math.max(r.height, 0);
    });
    return { media_area: mediaArea, total_area: totalArea, ratio: mediaArea / totalArea };
  }

  // -- interactive element inventory ---------------------------------------

  function accessibleName(el) {
    const ariaLabel = el.getAttribute("aria-label");
    if (ariaLabel && ariaLabel.trim()) return ariaLabel.trim();

    const labelledBy = el.getAttribute("aria-labelledby");
    if (labelledBy) {
      const parts = labelledBy
        .split(/\s+/)
        .map((id) => document.getElementById(id))
        .filter(Boolean)
        .map((e) => (e.innerText || "").trim())
        .filter(Boolean);
      if (parts.length) return parts.join(" ");
    }

    if (el.id) {
      try {
        const label = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
        if (label && (label.innerText || "").trim()) return label.innerText.trim();
      } catch (e) {
        /* invalid id for a CSS selector — ignore */
      }
    }

    const wrappingLabel = el.closest("label");
    if (wrappingLabel && (wrappingLabel.innerText || "").trim()) {
      return wrappingLabel.innerText.trim();
    }

    if ((el.innerText || "").trim()) return el.innerText.trim();
    if (el.getAttribute("title")) return el.getAttribute("title").trim();
    if (el.tagName === "IMG" && el.getAttribute("alt")) return el.getAttribute("alt").trim();
    if (el.value) return String(el.value);
    return "";
  }

  // For an unlabelled radio/checkbox, the option text usually sits in a
  // sibling or the parent container rather than in a <label>. This is the
  // exact case BUILD_SPEC §6.3 calls out as needing `nearby_text`.
  function nearbyText(el) {
    let sib = el.previousElementSibling;
    while (sib) {
      const t = (sib.innerText || "").trim();
      if (t) return t.slice(0, 200);
      sib = sib.previousElementSibling;
    }
    sib = el.nextElementSibling;
    while (sib) {
      const t = (sib.innerText || "").trim();
      if (t) return t.slice(0, 200);
      sib = sib.nextElementSibling;
    }
    const parentText = el.parentElement ? (el.parentElement.innerText || "").trim() : "";
    return parentText.slice(0, 200);
  }

  function isUnique(sel) {
    try {
      return document.querySelectorAll(sel).length === 1;
    } catch (e) {
      return false;
    }
  }

  // A selector that matches EXACTLY this element. Structural paths are built
  // from the element upward and checked for uniqueness after each step; if
  // no shorter form is unique the walk continues all the way to <html>, where
  // a path with nth-of-type at every level is unique by construction. (An
  // earlier version stopped after 6 levels, which on real, deeply nested
  // sites matched several different buttons and made clicks ambiguous.)
  function stableSelector(el) {
    if (el.id) {
      try {
        const byId = "#" + CSS.escape(el.id);
        if (isUnique(byId)) return byId;
      } catch (e) {
        /* fall through to a structural selector */
      }
    }
    const parts = [];
    let node = el;
    while (node && node.nodeType === 1) {
      let part = node.tagName.toLowerCase();
      const parent = node.parentElement;
      if (parent) {
        const siblings = Array.from(parent.children).filter((c) => c.tagName === node.tagName);
        if (siblings.length > 1) {
          part += `:nth-of-type(${siblings.indexOf(node) + 1})`;
        }
      }
      parts.unshift(part);
      const candidate = parts.join(" > ");
      if (node.tagName === "HTML" || isUnique(candidate)) return candidate;
      node = parent;
    }
    return parts.join(" > ");
  }

  function roleFor(el) {
    const explicit = el.getAttribute("role");
    if (explicit) return explicit;
    const tag = el.tagName.toLowerCase();
    if (tag === "a") return "link";
    if (tag === "button") return "button";
    if (tag === "select") return "combobox";
    if (tag === "textarea") return "textbox";
    if (tag === "input") {
      const type = (el.getAttribute("type") || "text").toLowerCase();
      if (type === "radio") return "radio";
      if (type === "checkbox") return "checkbox";
      if (type === "submit" || type === "button") return "button";
      return "textbox";
    }
    return "generic";
  }

  const INTERACTIVE_SELECTOR =
    "a, button, input, select, textarea, " +
    "[role=button], [role=link], [role=radio], [role=checkbox], " +
    "[role=option], [role=tab], [onclick], [tabindex]";

  function collectElements() {
    const out = [];
    const nodes = document.querySelectorAll(INTERACTIVE_SELECTOR);
    nodes.forEach((el) => {
      const tabindexAttr = el.getAttribute("tabindex");
      const isNativelyInteractive = ["A", "BUTTON", "INPUT", "SELECT", "TEXTAREA"].includes(
        el.tagName
      );
      if (
        tabindexAttr !== null &&
        parseInt(tabindexAttr, 10) === -1 &&
        !isNativelyInteractive &&
        !el.getAttribute("role") &&
        !el.hasAttribute("onclick")
      ) {
        return; // explicitly removed from tab order and not otherwise a control
      }
      if (!isVisible(el)) return;

      const rect = el.getBoundingClientRect();
      out.push({
        tag: el.tagName.toLowerCase(),
        role: roleFor(el),
        name: accessibleName(el),
        group_name: el.getAttribute("name") || null,
        text: (el.innerText || "").trim().slice(0, 300),
        value: "value" in el && el.value != null ? String(el.value) : null,
        state: {
          checked: !!el.checked,
          disabled: !!el.disabled || el.getAttribute("aria-disabled") === "true",
          selected: !!el.selected || el.getAttribute("aria-selected") === "true",
        },
        bbox: toBBox(rect),
        in_viewport:
          rect.top < window.innerHeight &&
          rect.bottom > 0 &&
          rect.left < window.innerWidth &&
          rect.right > 0,
        selector: stableSelector(el),
        nearby_text: nearbyText(el),
      });
    });
    return out;
  }

  return {
    text_blocks: collectTextBlocks(document.body),
    elements: collectElements(),
    media: computeMediaDominance(),
    page_height: document.documentElement.scrollHeight,
    page_width: document.documentElement.scrollWidth,
    dpr: window.devicePixelRatio || 1,
    url: location.href,
  };
})();
