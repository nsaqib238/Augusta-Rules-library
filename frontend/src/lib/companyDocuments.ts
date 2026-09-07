/**
 * Load documents visible to the signed-in user: own uploads + active company library.
 */
import { supabase } from './supabase';

type DocRow = {
  id: string;
  created_at?: string;
  [key: string]: unknown;
};

async function getActiveCompanyId(userId: string): Promise<string | null> {
  const { data: profile } = await supabase
    .from('profiles')
    .select('company_id')
    .eq('id', userId)
    .maybeSingle();

  if (profile?.company_id) {
    const { data: company } = await supabase
      .from('companies')
      .select('id, status')
      .eq('id', profile.company_id)
      .maybeSingle();
    if (company?.status === 'active') {
      return company.id as string;
    }
  }

  const { data: membership } = await supabase
    .from('company_memberships')
    .select('company_id')
    .eq('user_id', userId)
    .eq('status', 'active')
    .limit(1)
    .maybeSingle();

  if (!membership?.company_id) return null;

  const { data: company } = await supabase
    .from('companies')
    .select('id, status')
    .eq('id', membership.company_id)
    .maybeSingle();

  return company?.status === 'active' ? (company.id as string) : null;
}

export async function fetchVisibleDocuments<T extends object = DocRow>(
  userId: string,
  options?: {
    select?: string;
    discipline?: string;
    status?: string;
  }
): Promise<T[]> {
  const select = options?.select || '*';

  let ownQuery = supabase.from('documents').select(select).eq('user_id', userId);
  if (options?.discipline) {
    ownQuery = ownQuery.eq('discipline', options.discipline);
  }
  if (options?.status) {
    ownQuery = ownQuery.eq('status', options.status);
  }
  ownQuery = ownQuery.order('created_at', { ascending: false });

  const { data: ownData, error: ownError } = await ownQuery;
  if (ownError) {
    throw new Error(ownError.message);
  }

  const docs = (ownData ? [...ownData] : []) as unknown as T[];
  const seen = new Set(
    docs.map((d) => String((d as { id?: string }).id ?? ''))
  );

  const companyId = await getActiveCompanyId(userId);
  if (!companyId) {
    return docs;
  }

  let companyQuery = supabase.from('documents').select(select).eq('company_id', companyId);
  if (options?.discipline) {
    companyQuery = companyQuery.eq('discipline', options.discipline);
  }
  if (options?.status) {
    companyQuery = companyQuery.eq('status', options.status);
  }
  companyQuery = companyQuery.order('created_at', { ascending: false });

  const { data: companyData, error: companyError } = await companyQuery;
  if (companyError) {
    console.warn('Company documents fetch failed:', companyError.message);
    return docs;
  }

  for (const row of companyData || []) {
    const d = row as unknown as T;
    const id = String((d as { id?: string }).id ?? '');
    if (id && !seen.has(id)) {
      docs.push(d);
      seen.add(id);
    }
  }

  docs.sort((a, b) => {
    const ac = String((a as { created_at?: string }).created_at || '');
    const bc = String((b as { created_at?: string }).created_at || '');
    return bc.localeCompare(ac);
  });

  return docs;
}
