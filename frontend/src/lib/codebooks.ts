export type Discipline = 'electrical' | 'mechanical' | 'fire' | 'hydraulics';

export type StandardFamily =
  | 'AS_NZS'
  | 'ISO'
  | 'IEC'
  | 'ASTM'
  | 'NFPA'
  | 'API'
  | 'IEEE'
  | 'NBN';

export interface CodebookOption {
  id: string;
  label: string;
  discipline: Discipline;
  /** Parser family; defaults from id/label when omitted. */
  family?: StandardFamily;
}

/** Publisher / parser families for upload dropdown. */
/** Publisher / parser families (backend parsing only — not shown on user upload). */
export const STANDARD_FAMILIES: { id: StandardFamily; label: string }[] = [
  { id: 'AS_NZS', label: 'Generic / dotted clause style' },
  { id: 'IEC', label: 'IEC-style numbering' },
  { id: 'ISO', label: 'ISO-style numbering' },
  { id: 'ASTM', label: 'ASTM-style numbering' },
  { id: 'NFPA', label: 'NFPA-style numbering' },
  { id: 'API', label: 'API-style numbering' },
  { id: 'IEEE', label: 'IEEE-style numbering' },
  { id: 'NBN', label: 'Telecom guideline numbering' },
];

/** Fallback parser family when the code name does not match a known pattern. */
export const DEFAULT_STANDARD_FAMILY: StandardFamily = 'AS_NZS';

export function inferFamilyFromCodebookId(id: string, label?: string): StandardFamily {
  const cid = (id || '').toUpperCase();
  if (cid.startsWith('NBN')) return 'NBN';
  if (cid.startsWith('IEC')) return 'IEC';
  if (cid.startsWith('ISO')) return 'ISO';
  if (cid.startsWith('ASTM')) return 'ASTM';
  if (cid.startsWith('NFPA')) return 'NFPA';
  if (cid.startsWith('API')) return 'API';
  if (cid.startsWith('IEEE')) return 'IEEE';
  const text = `${label || ''} ${id}`;
  if (/\bNBN\b/i.test(text)) return 'NBN';
  if (/\b(MDU|SDU)\b/i.test(text) && /\b(NBN|TELECOM|FIBRE|FIBER)\b/i.test(text)) return 'NBN';
  if (/\bIEC\b/i.test(text)) return 'IEC';
  if (/\bISO\b/i.test(text)) return 'ISO';
  if (/\bASTM\b/i.test(text)) return 'ASTM';
  if (/\bNFPA\b/i.test(text)) return 'NFPA';
  if (/\bAPI\b/i.test(text)) return 'API';
  if (/\bIEEE\b/i.test(text)) return 'IEEE';
  return 'AS_NZS';
}

export function codebookFamily(c: CodebookOption): StandardFamily {
  return c.family ?? inferFamilyFromCodebookId(c.id, c.label);
}

/** User-upload standards + shared-library ids (for labels). */
export const CODEBOOKS: CodebookOption[] = [
  // ⚡ Electrical — AS/NZS
  { id: 'AS3000', label: 'AS/NZS 3000', discipline: 'electrical', family: 'AS_NZS' },
  { id: 'AS3003', label: 'AS/NZS 3003', discipline: 'electrical', family: 'AS_NZS' },
  { id: 'AS3017', label: 'AS/NZS 3017', discipline: 'electrical', family: 'AS_NZS' },
  { id: 'AS3008_1_1', label: 'AS/NZS 3008.1.1', discipline: 'electrical', family: 'AS_NZS' },
  { id: 'AS3012', label: 'AS/NZS 3012', discipline: 'electrical', family: 'AS_NZS' },
  { id: 'AS3010', label: 'AS/NZS 3010', discipline: 'electrical', family: 'AS_NZS' },
  { id: 'AS3015', label: 'AS/NZS 3015', discipline: 'electrical', family: 'AS_NZS' },
  { id: 'AS3019', label: 'AS/NZS 3019', discipline: 'electrical', family: 'AS_NZS' },
  { id: 'AS3001', label: 'AS/NZS 3001', discipline: 'electrical', family: 'AS_NZS' },
  { id: 'AS3007', label: 'AS/NZS 3007', discipline: 'electrical', family: 'AS_NZS' },
  { id: 'AS5033', label: 'AS/NZS 5033', discipline: 'electrical', family: 'AS_NZS' },
  { id: 'AS4777_1', label: 'AS/NZS 4777.1', discipline: 'electrical', family: 'AS_NZS' },
  { id: 'AS4777_2', label: 'AS/NZS 4777.2', discipline: 'electrical', family: 'AS_NZS' },
  { id: 'AS5139', label: 'AS/NZS 5139', discipline: 'electrical', family: 'AS_NZS' },
  { id: 'AS61439', label: 'AS/NZS 61439 (Switchboards)', discipline: 'electrical', family: 'AS_NZS' },
  { id: 'AS1768', label: 'AS/NZS 1768 (Lightning)', discipline: 'electrical', family: 'AS_NZS' },
  { id: 'AS2067', label: 'AS 2067 (HV)', discipline: 'electrical', family: 'AS_NZS' },
  { id: 'AS1158', label: 'AS/NZS 1158 (Road lighting)', discipline: 'electrical', family: 'AS_NZS' },
  // ⚡ Electrical — international
  { id: 'IEC61439', label: 'IEC 61439', discipline: 'electrical', family: 'IEC' },
  { id: 'ISO50001', label: 'ISO 50001 (Energy management)', discipline: 'electrical', family: 'ISO' },
  { id: 'IEEE1584', label: 'IEEE 1584 (Arc flash)', discipline: 'electrical', family: 'IEEE' },
  { id: 'IEEE519', label: 'IEEE 519 (Harmonics)', discipline: 'electrical', family: 'IEEE' },
  { id: 'NBN_MDU', label: 'NBN MDU Guidelines', discipline: 'electrical', family: 'NBN' },
  { id: 'NBN_SDU', label: 'NBN SDU Guidelines', discipline: 'electrical', family: 'NBN' },
  { id: 'NBN_TELECOM', label: 'NBN Telecom / Pit & Pipe Guidelines', discipline: 'electrical', family: 'NBN' },

  // 🔥 Fire — AS/NZS
  { id: 'AS1670_1', label: 'AS 1670.1', discipline: 'fire', family: 'AS_NZS' },
  { id: 'AS1670_3', label: 'AS 1670.3', discipline: 'fire', family: 'AS_NZS' },
  { id: 'AS2118_1', label: 'AS 2118.1', discipline: 'fire', family: 'AS_NZS' },
  { id: 'AS2118_4', label: 'AS 2118.4', discipline: 'fire', family: 'AS_NZS' },
  { id: 'AS2419_1', label: 'AS 2419.1', discipline: 'fire', family: 'AS_NZS' },
  { id: 'AS1851', label: 'AS 1851', discipline: 'fire', family: 'AS_NZS' },
  { id: 'AS2293_1', label: 'AS 2293.1', discipline: 'fire', family: 'AS_NZS' },
  { id: 'AS2293_2', label: 'AS 2293.2', discipline: 'fire', family: 'AS_NZS' },
  { id: 'AS2293_3', label: 'AS 2293.3', discipline: 'fire', family: 'AS_NZS' },
  { id: 'AS4428', label: 'AS 4428 Series', discipline: 'fire', family: 'AS_NZS' },
  { id: 'AS7240', label: 'AS 7240 Series', discipline: 'fire', family: 'AS_NZS' },
  { id: 'AS1530', label: 'AS 1530 Series', discipline: 'fire', family: 'AS_NZS' },
  { id: 'AS4072_1', label: 'AS 4072.1', discipline: 'fire', family: 'AS_NZS' },
  { id: 'AS1530_4', label: 'AS 1530.4', discipline: 'fire', family: 'AS_NZS' },
  // 🔥 Fire — NFPA
  { id: 'NFPA13', label: 'NFPA 13 (Sprinklers)', discipline: 'fire', family: 'NFPA' },
  { id: 'NFPA72', label: 'NFPA 72 (Fire alarm)', discipline: 'fire', family: 'NFPA' },
  { id: 'NFPA101', label: 'NFPA 101 (Life safety)', discipline: 'fire', family: 'NFPA' },

  // 💧 Hydraulics — AS/NZS
  { id: 'AS3500_0', label: 'AS/NZS 3500.0', discipline: 'hydraulics', family: 'AS_NZS' },
  { id: 'AS3500_1', label: 'AS/NZS 3500.1', discipline: 'hydraulics', family: 'AS_NZS' },
  { id: 'AS3500_2', label: 'AS/NZS 3500.2', discipline: 'hydraulics', family: 'AS_NZS' },
  { id: 'AS3500_3', label: 'AS/NZS 3500.3', discipline: 'hydraulics', family: 'AS_NZS' },
  { id: 'AS3500_4', label: 'AS/NZS 3500.4', discipline: 'hydraulics', family: 'AS_NZS' },
  { id: 'AS1546', label: 'AS 1546', discipline: 'hydraulics', family: 'AS_NZS' },
  { id: 'AS1547', label: 'AS 1547', discipline: 'hydraulics', family: 'AS_NZS' },
  { id: 'AS6400', label: 'AS/NZS 6400', discipline: 'hydraulics', family: 'AS_NZS' },
  { id: 'AS3688', label: 'AS 3688', discipline: 'hydraulics', family: 'AS_NZS' },
  { id: 'AS2845', label: 'AS 2845 Series', discipline: 'hydraulics', family: 'AS_NZS' },
  // 💧 Hydraulics — API
  { id: 'API650', label: 'API 650 (Welded tanks)', discipline: 'hydraulics', family: 'API' },
  { id: 'API620', label: 'API 620 (Low-pressure tanks)', discipline: 'hydraulics', family: 'API' },

  // ❄ Mechanical — AS/NZS
  { id: 'AS1668_1', label: 'AS 1668.1', discipline: 'mechanical', family: 'AS_NZS' },
  { id: 'AS1668_2', label: 'AS 1668.2', discipline: 'mechanical', family: 'AS_NZS' },
  { id: 'AS3666_1', label: 'AS/NZS 3666.1', discipline: 'mechanical', family: 'AS_NZS' },
  { id: 'AS3666_2', label: 'AS/NZS 3666.2', discipline: 'mechanical', family: 'AS_NZS' },
  { id: 'AS3666_3', label: 'AS/NZS 3666.3', discipline: 'mechanical', family: 'AS_NZS' },
  { id: 'AS3666_4', label: 'AS/NZS 3666.4', discipline: 'mechanical', family: 'AS_NZS' },
  { id: 'AS4254', label: 'AS/NZS 4254 Series', discipline: 'mechanical', family: 'AS_NZS' },
  { id: 'AS4254_1', label: 'AS 4254.1', discipline: 'mechanical', family: 'AS_NZS' },
  { id: 'AS4254_2', label: 'AS 4254.2', discipline: 'mechanical', family: 'AS_NZS' },
  { id: 'AS4254_3', label: 'AS 4254.3', discipline: 'mechanical', family: 'AS_NZS' },
  { id: 'AS4254_4', label: 'AS 4254.4', discipline: 'mechanical', family: 'AS_NZS' },
  { id: 'AS1324', label: 'AS 1324', discipline: 'mechanical', family: 'AS_NZS' },
  { id: 'AS1851_HVAC', label: 'AS 1851 (HVAC fire systems)', discipline: 'mechanical', family: 'AS_NZS' },
  // ❄ Mechanical — ISO / ASTM
  { id: 'ISO16890', label: 'ISO 16890 (Air filters)', discipline: 'mechanical', family: 'ISO' },
  { id: 'ASTM_A36', label: 'ASTM A36 (Structural steel)', discipline: 'mechanical', family: 'ASTM' },

  // Shared library (admin upload — not shown on discipline upload/Q&A tabs)
  { id: 'NCC2022_VOL1', label: 'NCC 2022 Vol 1 — Class 2–9', discipline: 'fire', family: 'AS_NZS' },
  { id: 'NCC2022_VOL2', label: 'NCC 2022 Vol 2 — Class 1 & 10', discipline: 'fire', family: 'AS_NZS' },
  { id: 'NCC2022_VOL3', label: 'NCC 2022 Vol 3 — Plumbing Code', discipline: 'hydraulics', family: 'AS_NZS' },
  { id: 'NSW_SIR_2018', label: 'NSW SIR 2018', discipline: 'electrical', family: 'AS_NZS' },
  { id: 'SA_SIR_2025', label: 'South Australia SIR 2025', discipline: 'electrical', family: 'AS_NZS' },
  { id: 'TASNETWORK_SIR_V85', label: 'TasNetwork SIR V8-5', discipline: 'electrical', family: 'AS_NZS' },
  { id: 'VIC_SIR_2025', label: 'Victorian SIR 2025', discipline: 'electrical', family: 'AS_NZS' },
];

export const OTHER_CODEBOOK = 'OTHER';

export const SIR_CODEBOOK_IDS = [
  'NSW_SIR_2018',
  'SA_SIR_2025',
  'TASNETWORK_SIR_V85',
  'VIC_SIR_2025',
] as const;

export const NCC_CODEBOOK_IDS = ['NCC2022_VOL1', 'NCC2022_VOL2', 'NCC2022_VOL3'] as const;

export type SharedLibraryFamily = 'sir' | 'ncc';

const SHARED_LIBRARY_CODEBOOK_IDS = new Set<string>([...SIR_CODEBOOK_IDS, ...NCC_CODEBOOK_IDS]);

export function codebooksForDiscipline(discipline: string, family?: StandardFamily): CodebookOption[] {
  const d = discipline as Discipline;
  return CODEBOOKS.filter(
    (c) => c.discipline === d && (!family || codebookFamily(c) === family),
  );
}

/** User PDF upload / discipline Q&A — same discipline, excludes admin NCC/SIR library. */
export function codebooksForUserUploads(discipline: string, family?: StandardFamily): CodebookOption[] {
  return codebooksForDiscipline(discipline, family).filter((c) => !SHARED_LIBRARY_CODEBOOK_IDS.has(c.id));
}

export function defaultCodebookForDiscipline(discipline: string, family?: StandardFamily): string {
  const list = codebooksForDiscipline(discipline, family);
  return list[0]?.id ?? OTHER_CODEBOOK;
}

export function defaultUserCodebookForDiscipline(discipline: string, family?: StandardFamily): string {
  const list = codebooksForUserUploads(discipline, family);
  return list[0]?.id ?? OTHER_CODEBOOK;
}

export function labelForCodebookId(id: string): string {
  return CODEBOOKS.find((c) => c.id === id)?.label ?? id;
}

export function familyForCodebookId(id: string): StandardFamily {
  const entry = CODEBOOKS.find((c) => c.id === id);
  if (entry) return codebookFamily(entry);
  return inferFamilyFromCodebookId(id);
}

export function disciplineMeta(discipline: Discipline) {
  const map: Record<Discipline, { name: string; icon: string }> = {
    electrical: { name: 'Electrical', icon: '⚡' },
    mechanical: { name: 'Mechanical (HVAC)', icon: '❄' },
    fire: { name: 'Fire Safety', icon: '🔥' },
    hydraulics: { name: 'Hydraulics', icon: '💧' },
  };
  return map[discipline];
}
