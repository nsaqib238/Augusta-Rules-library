import React, { useState } from 'react';
import { Download, Copy, Image } from 'lucide-react';
import {
  copyQaAnswerPlainText,
  exportQaAnswerAsPdf,
  findQuestionForAssistantMessage,
  QaExportPayload,
  QaExportProfile,
  QaExportSource,
} from '../../lib/qaAnswerExport';
import { exportQaAnswerAsJpeg } from '../../lib/qaSocialImageExport';

interface QaMessageLike {
  id: string;
  type: string;
  content: string;
  timestamp: Date;
  confidence?: number;
  retrievalNote?: string;
  sources?: QaExportSource[];
}

interface QaExportActionsProps {
  message: QaMessageLike;
  messages: QaMessageLike[];
  profile?: QaExportProfile;
  productTitle?: string;
  productTagline?: string;
  documentSubtitle: string;
}

export const QaExportActions: React.FC<QaExportActionsProps> = ({
  message,
  messages,
  profile = 'qna',
  productTitle,
  productTagline,
  documentSubtitle,
}) => {
  const [exportingPdf, setExportingPdf] = useState(false);
  const [exportingImage, setExportingImage] = useState(false);

  const buildPayload = (): QaExportPayload | null => {
    const question = findQuestionForAssistantMessage(messages, message.id);
    if (!question.trim()) return null;
    return {
      profile,
      productTitle,
      productTagline,
      documentSubtitle,
      question: question.trim(),
      answerMarkdown: message.content,
      answeredAt: message.timestamp,
      confidence: message.confidence,
      retrievalNote: message.retrievalNote,
      sources: message.sources,
    };
  };

  const onExportPdf = async () => {
    const payload = buildPayload();
    if (!payload) {
      alert('Could not find the question for this answer.');
      return;
    }
    setExportingPdf(true);
    try {
      await exportQaAnswerAsPdf(payload);
    } catch (err) {
      console.error('PDF export failed:', err);
      alert('Could not create PDF. Try again in a moment.');
    } finally {
      setExportingPdf(false);
    }
  };

  const onCopy = () => {
    const payload = buildPayload();
    if (!payload) {
      alert('Could not find the question for this answer.');
      return;
    }
    copyQaAnswerPlainText(payload);
    alert('Copied to clipboard.');
  };

  const onExportJpeg = async () => {
    const payload = buildPayload();
    if (!payload) {
      alert('Could not find the question for this answer.');
      return;
    }
    setExportingImage(true);
    try {
      await exportQaAnswerAsJpeg(payload);
    } catch (err) {
      console.error('JPG export failed:', err);
      alert('Could not create image. Try again or use Export PDF.');
    } finally {
      setExportingImage(false);
    }
  };

  return (
    <div className="flex flex-wrap items-center gap-2">
      <button
        type="button"
        onClick={() => void onExportPdf()}
        disabled={exportingPdf}
        className="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs font-medium text-slate-700 shadow-sm transition hover:bg-slate-50 disabled:cursor-wait disabled:opacity-60"
        title="Download a PDF copy of this answer"
      >
        <Download className="h-3.5 w-3.5" />
        {exportingPdf ? 'Creating PDF…' : 'Export PDF'}
      </button>
      <button
        type="button"
        onClick={onExportJpeg}
        disabled={exportingImage}
        className="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs font-medium text-slate-700 shadow-sm transition hover:bg-slate-50 disabled:cursor-wait disabled:opacity-60"
        title="Download a branded JPG for Facebook or social posts (1200px wide)"
      >
        <Image className="h-3.5 w-3.5" />
        {exportingImage ? 'Creating JPG…' : 'Export JPG'}
      </button>
      <button
        type="button"
        onClick={onCopy}
        className="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs font-medium text-slate-700 shadow-sm transition hover:bg-slate-50"
        title="Copy question and answer to clipboard"
      >
        <Copy className="h-3.5 w-3.5" />
        Copy
      </button>
    </div>
  );
};
