import React from 'react';

export type DesignScope = {
  deliverables?: string[];
  design_checks?: string[];
  coordination?: string[];
  confirm?: string[];
};

const SCOPE_SECTIONS: Array<{ key: keyof DesignScope; label: string }> = [
  { key: 'deliverables', label: 'Drawings & specifications' },
  { key: 'design_checks', label: 'Design checks (clearances, sizing, access)' },
  { key: 'coordination', label: 'Coordinate with' },
  { key: 'confirm', label: 'Confirm before detailing' },
];

function hasDesignScope(scope?: DesignScope): boolean {
  if (!scope) return false;
  return SCOPE_SECTIONS.some((section) => (scope[section.key]?.length ?? 0) > 0);
}

type DesignScopeBlockProps = {
  scope?: DesignScope;
  fallbackActions?: string[];
};

export const DesignScopeBlock: React.FC<DesignScopeBlockProps> = ({ scope, fallbackActions }) => {
  if (hasDesignScope(scope)) {
    return (
      <div className="mt-4 space-y-3 rounded-2xl border border-[#e8dcc0] bg-[#fffaf0] p-4">
        <p className="text-xs font-semibold uppercase tracking-wide text-[#9a7a35]">
          Your design package should address
        </p>
        {SCOPE_SECTIONS.map((section) => {
          const items = scope?.[section.key] ?? [];
          if (items.length === 0) return null;
          return (
            <div key={section.key}>
              <p className="text-xs font-semibold text-slate-800">{section.label}</p>
              <ul className="mt-1 list-disc space-y-1 pl-5 text-xs leading-5 text-slate-700">
                {items.map((item) => (
                  <li key={`${section.key}-${item}`}>{item}</li>
                ))}
              </ul>
            </div>
          );
        })}
      </div>
    );
  }

  if (!fallbackActions?.length) return null;

  return (
    <div className="mt-4">
      <p className="text-xs font-semibold text-slate-800">Address in drawings/specs</p>
      <ul className="mt-1 list-disc space-y-1 pl-5 text-xs leading-5 text-slate-700">
        {fallbackActions.map((action) => (
          <li key={action}>{action}</li>
        ))}
      </ul>
    </div>
  );
};

export default DesignScopeBlock;
