/**
 * Render styled HTML export documents as a direct .pdf download in the browser.
 */

function ensurePdfFilename(filename: string): string {
  const trimmed = filename.trim();
  if (!trimmed) return 'export.pdf';
  return trimmed.toLowerCase().endsWith('.pdf') ? trimmed : `${trimmed}.pdf`;
}

function waitForFrameDocument(doc: Document): Promise<void> {
  return new Promise((resolve) => {
    const finish = () => {
      window.setTimeout(() => resolve(), 250);
    };
    if (doc.readyState === 'complete') {
      finish();
      return;
    }
    doc.defaultView?.addEventListener('load', finish, { once: true });
    window.setTimeout(() => resolve(), 2000);
  });
}

const PDF_AVOID_SELECTOR =
  '.pdf-avoid-break, .pdf-topic-block, .check-item, .scope-block, .header, .brief-block, .scoping, .footer';

function prepareDocumentForPdf(doc: Document): void {
  const root = doc.body;
  if (!root) return;

  const style = doc.createElement('style');
  style.textContent = `
    .pdf-avoid-break,
    .pdf-topic-block {
      page-break-inside: avoid;
      break-inside: avoid-page;
    }
    .report-body h2,
    .report-body h3 {
      page-break-after: avoid;
      break-after: avoid-page;
    }
  `;
  doc.head.appendChild(style);

  root.querySelectorAll('p, h1, h2, h3, h4, ul, ol, li, article, blockquote').forEach((el) => {
    el.classList.add('pdf-avoid-break');
  });
  root.querySelectorAll('.check-item, .scope-block, .header, .brief-block, .scoping, .footer').forEach((el) => {
    el.classList.add('pdf-avoid-break');
  });

  const h3s = Array.from(root.querySelectorAll('h3'));
  for (const h3 of h3s) {
    if (h3.parentElement?.classList.contains('pdf-topic-block')) continue;
    const wrapper = doc.createElement('div');
    wrapper.className = 'pdf-topic-block pdf-avoid-break';
    h3.parentNode?.insertBefore(wrapper, h3);
    wrapper.appendChild(h3);
    let node: ChildNode | null = wrapper.nextSibling;
    while (node) {
      if (node instanceof HTMLElement && (node.tagName === 'H3' || node.tagName === 'H2')) break;
      const next = node.nextSibling;
      wrapper.appendChild(node);
      node = next;
    }
  }
}

export async function downloadHtmlAsPdf(html: string, filename: string): Promise<void> {
  const iframe = document.createElement('iframe');
  iframe.setAttribute('title', 'PDF export');
  iframe.setAttribute('aria-hidden', 'true');
  iframe.style.cssText =
    'position:fixed;left:-10000px;top:0;width:794px;min-height:1123px;border:0;visibility:hidden;';

  document.body.appendChild(iframe);

  const doc = iframe.contentDocument;
  if (!doc) {
    document.body.removeChild(iframe);
    throw new Error('Could not prepare PDF export.');
  }

  doc.open();
  doc.write(html);
  doc.close();

  try {
    await waitForFrameDocument(doc);
    prepareDocumentForPdf(doc);

    const html2pdf = (await import('html2pdf.js')).default;
    const pdfOptions = {
        margin: [14, 12, 14, 12] as [number, number, number, number],
        filename: ensurePdfFilename(filename),
        image: { type: 'jpeg' as const, quality: 0.95 },
        html2canvas: {
          scale: 2,
          useCORS: true,
          logging: false,
          scrollY: 0,
          windowWidth: doc.documentElement.scrollWidth || 794,
        },
        jsPDF: { unit: 'mm', format: 'a4', orientation: 'portrait' },
        pagebreak: {
          mode: ['css', 'legacy'],
          avoid: PDF_AVOID_SELECTOR.split(',').map((selector) => selector.trim()),
        },
      };

    await html2pdf()
      .set(pdfOptions as never)
      .from(doc.body)
      .save();
  } finally {
    if (iframe.parentNode) {
      iframe.parentNode.removeChild(iframe);
    }
  }
}
