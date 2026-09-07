import React from 'react';
import { headerBrandLogoUrl } from '../lib/brandLogo';

/** Intrinsic pixels of `public/img/AugustaSearch-wordmark.png` (drives aspect ratio). */
const WORDMARK_WIDTH = 823;
const WORDMARK_HEIGHT = 293;

type BrandLogoVariant = 'header' | 'auth';

const variantStyles: Record<
  BrandLogoVariant,
  { shell: string; img: string }
> = {
  header: {
    shell:
      'inline-flex shrink-0 items-center rounded-2xl border border-white/70 bg-white px-2.5 py-1.5 shadow-[0_18px_40px_rgba(15,23,42,0.12)]',
    // Explicit width + height (not w-auto) — Chrome otherwise shrinks the img in flex rows.
    img: 'block h-10 w-[7.03rem] min-h-10 flex-none max-w-none object-contain object-left sm:h-11 sm:w-[7.73rem] sm:min-h-11',
  },
  auth: {
    shell:
      'inline-flex shrink-0 items-center rounded-2xl border border-white/15 bg-white px-2.5 py-1.5 shadow-2xl shadow-black/20',
    img: 'block h-12 w-[8.43rem] min-h-12 flex-none max-w-none object-contain object-left sm:h-14 sm:w-[9.83rem] sm:min-h-14',
  },
};

interface BrandLogoProps {
  variant?: BrandLogoVariant;
  className?: string;
}

/** Augusta Search wordmark — uses cropped PNG so local and production match. */
export const BrandLogo: React.FC<BrandLogoProps> = ({ variant = 'header', className = '' }) => {
  const v = variantStyles[variant];
  return (
    <div className={`${v.shell} ${className}`}>
      <img
        src={headerBrandLogoUrl}
        alt="Augusta Search"
        width={WORDMARK_WIDTH}
        height={WORDMARK_HEIGHT}
        className={v.img}
        draggable={false}
      />
    </div>
  );
};
