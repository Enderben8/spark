/*
 * Spark fixture site — canvas text rendering helpers.
 *
 * Used by passage-canvas.html, passage-canvas-condensed.html and
 * passage-canvas-columns.html to draw the passage text directly onto a
 * <canvas> with fillText, so there is no selectable/DOM text for the
 * passage body. This is the OCR test case described in BUILD_SPEC.md
 * sections 11.1 and 6.4.1.
 */
(function (global) {
  "use strict";

  function wrapText(ctx, text, maxWidth) {
    var words = text.split(" ");
    var lines = [];
    var current = "";
    for (var i = 0; i < words.length; i++) {
      var test = current ? current + " " + words[i] : words[i];
      if (ctx.measureText(test).width > maxWidth && current) {
        lines.push(current);
        current = words[i];
      } else {
        current = test;
      }
    }
    if (current) {
      lines.push(current);
    }
    return lines;
  }

  // Draws text one character at a time so an artificial letter-spacing can
  // be applied (canvas fillText has no built-in letter-spacing in older
  // browsers). Passing 0/undefined spacing just falls back to fillText.
  function fillTextSpaced(ctx, text, x, y, letterSpacing) {
    if (!letterSpacing) {
      ctx.fillText(text, x, y);
      return;
    }
    var cx = x;
    for (var i = 0; i < text.length; i++) {
      var ch = text[i];
      ctx.fillText(ch, cx, y);
      cx += ctx.measureText(ch).width + letterSpacing;
    }
  }

  // Wraps every paragraph and returns a flat array of lines, with `null`
  // marking a paragraph break (a bit of extra vertical space).
  function buildLines(ctx, paragraphs, maxWidth) {
    var allLines = [];
    paragraphs.forEach(function (p) {
      wrapText(ctx, p, maxWidth).forEach(function (line) {
        allLines.push(line);
      });
      allLines.push(null);
    });
    return allLines;
  }

  // Renders the passage as a single column. Sizes the canvas to fit all of
  // the wrapped text, so the resulting page is taller than the viewport
  // exactly like the DOM passage.
  function renderPassageSingleColumn(canvas, opts) {
    opts = opts || {};
    var ctx = canvas.getContext("2d");
    var width = opts.width || 760;
    var fontSize = opts.fontSize || 20;
    var fontFamily = opts.fontFamily || "sans-serif";
    var letterSpacing = opts.letterSpacing || 0;
    var lineHeight = fontSize * (opts.lineHeightMult || 1.5);
    var paraGap = lineHeight * 0.6;
    var padding = 24;
    var titleFontSize = opts.titleFontSize || 26;

    ctx.font = fontSize + "px " + fontFamily;
    var lines = buildLines(ctx, opts.paragraphs, width - padding * 2);

    var titleBlock = opts.title ? titleFontSize + 30 : 0;
    var contentHeight = lines.length * lineHeight + titleBlock;
    canvas.width = width;
    canvas.height = Math.ceil(contentHeight + padding * 2);

    // Resizing the canvas clears the context state, so font must be reset.
    ctx.fillStyle = "#ffffff";
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.fillStyle = "#111111";
    ctx.textBaseline = "top";

    var y = padding;
    if (opts.title) {
      ctx.font = "bold " + titleFontSize + "px sans-serif";
      ctx.fillText(opts.title, padding, y);
      y += titleBlock;
    }

    ctx.font = fontSize + "px " + fontFamily;
    lines.forEach(function (line) {
      if (line === null) {
        y += paraGap;
        return;
      }
      fillTextSpaced(ctx, line, padding, y, letterSpacing);
      y += lineHeight;
    });

    return canvas.height;
  }

  // Renders the passage as two side-by-side newspaper-style columns. This
  // is the OCR reading-order trap called out in BUILD_SPEC.md 6.4.1: naive
  // OCR reads straight across both columns and interleaves them, without
  // lowering its confidence score.
  function renderPassageTwoColumns(canvas, opts) {
    opts = opts || {};
    var ctx = canvas.getContext("2d");
    var width = opts.width || 1000;
    var fontSize = opts.fontSize || 17;
    var fontFamily = opts.fontFamily || "sans-serif";
    var lineHeight = fontSize * (opts.lineHeightMult || 1.5);
    var paraGap = lineHeight * 0.6;
    var padding = 24;
    var gutter = 40;
    var titleFontSize = opts.titleFontSize || 26;
    var colWidth = (width - padding * 2 - gutter) / 2;

    ctx.font = fontSize + "px " + fontFamily;
    var lines = buildLines(ctx, opts.paragraphs, colWidth);
    var half = Math.ceil(lines.length / 2);
    var col1 = lines.slice(0, half);
    var col2 = lines.slice(half);
    var maxRows = Math.max(col1.length, col2.length);

    var titleBlock = opts.title ? titleFontSize + 30 : 0;
    var contentHeight = maxRows * lineHeight + titleBlock;
    canvas.width = width;
    canvas.height = Math.ceil(contentHeight + padding * 2);

    ctx.fillStyle = "#ffffff";
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.fillStyle = "#111111";
    ctx.textBaseline = "top";

    var y0 = padding;
    if (opts.title) {
      ctx.font = "bold " + titleFontSize + "px sans-serif";
      ctx.fillText(opts.title, padding, y0);
      y0 += titleBlock;
    }

    ctx.font = fontSize + "px " + fontFamily;

    var x1 = padding;
    var x2 = padding + colWidth + gutter;

    // Column divider, purely visual.
    ctx.strokeStyle = "#cccccc";
    ctx.beginPath();
    ctx.moveTo(padding + colWidth + gutter / 2, y0);
    ctx.lineTo(padding + colWidth + gutter / 2, canvas.height - padding);
    ctx.stroke();

    var y = y0;
    col1.forEach(function (line) {
      if (line === null) {
        y += paraGap;
        return;
      }
      ctx.fillText(line, x1, y);
      y += lineHeight;
    });

    y = y0;
    col2.forEach(function (line) {
      if (line === null) {
        y += paraGap;
        return;
      }
      ctx.fillText(line, x2, y);
      y += lineHeight;
    });

    return canvas.height;
  }

  global.SparkCanvasPassage = {
    wrapText: wrapText,
    renderPassageSingleColumn: renderPassageSingleColumn,
    renderPassageTwoColumns: renderPassageTwoColumns
  };
})(window);
