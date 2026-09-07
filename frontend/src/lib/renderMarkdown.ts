/** Lightweight markdown for practitioner answers (headers, bold, bullets, inline code). */
export function renderPractitionerMarkdown(markdown: string): string {
  const escaped = markdown
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');

  const parts: string[] = [];
  let bulletBuffer: string[] = [];

  const flushBullets = () => {
    if (!bulletBuffer.length) return;
    const items = bulletBuffer
      .map((line) => `<li>${inlineFormat(line.replace(/^-\s*/, ''))}</li>`)
      .join('');
    parts.push(`<ul class="my-2 list-disc space-y-1 pl-5">${items}</ul>`);
    bulletBuffer = [];
  };

  for (const rawLine of escaped.split('\n')) {
    const trimmed = rawLine.trim();
    if (!trimmed) {
      flushBullets();
      continue;
    }

    if (/^-\s/.test(trimmed)) {
      bulletBuffer.push(trimmed);
      continue;
    }

    flushBullets();

    if (/^###\s/.test(trimmed)) {
      parts.push(
        `<h3 class="mt-4 mb-2 text-base font-semibold text-slate-900">${inlineFormat(trimmed.replace(/^###\s*/, ''))}</h3>`
      );
      continue;
    }
    if (/^##\s/.test(trimmed)) {
      parts.push(
        `<h2 class="mt-5 mb-2 text-lg font-semibold text-slate-950">${inlineFormat(trimmed.replace(/^##\s*/, ''))}</h2>`
      );
      continue;
    }
    if (/^#\s/.test(trimmed)) {
      parts.push(
        `<h1 class="mb-3 text-xl font-bold text-slate-950">${inlineFormat(trimmed.replace(/^#\s*/, ''))}</h1>`
      );
      continue;
    }

    parts.push(`<p class="my-2 leading-relaxed text-slate-800">${inlineFormat(trimmed)}</p>`);
  }

  flushBullets();
  return parts.join('');
}

function inlineFormat(text: string): string {
  return inlineCode(inlineBold(text));
}

function inlineBold(text: string): string {
  return text.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');
}

function inlineCode(text: string): string {
  return text.replace(/`([^`]+)`/g, '<code class="rounded bg-slate-100 px-1 py-0.5 text-xs text-slate-700">$1</code>');
}
