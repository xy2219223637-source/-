import { api } from './client';

export type ModelOrder = {
  id: string; request_id: string; tenant_id: string; user_id: string; model_key: string; provider: string; model_id: string;
  execution_mode: 'MOCK' | 'REAL'; status: 'RESERVED' | 'SETTLED' | 'CANCELED'; reconciliation_status: 'NONE' | 'NEEDS_REVIEW' | 'RESOLVED';
  usage: { source: 'REAL_TOKENS' | 'FOLDED_TOKENS' | 'UNKNOWN'; input_tokens: number | null; output_tokens: number | null;
    converted_tokens: string | null; raw_metric: 'image_count' | 'audio_milliseconds' | 'character_count' | null; raw_quantity: number | null };
  supplier_cost: { status: 'KNOWN' | 'UNKNOWN' | 'NOT_INCURRED'; amount: string | null; currency: string | null };
  charged_credits: string; reserved_credits: string; created_at: string; provider_request_id: string | null;
  pricing_snapshot: { usage_mode: 'REAL_TOKENS' | 'FOLDED_TOKENS'; metrics: { metric: string; unit_size: number; credits_per_unit: string }[];
    conversion: { source_metric: string; source_unit_size: number; tokens_per_unit: number } | null };
};
export type ModelOrderPage = { items: ModelOrder[]; total: number; page: number; page_size: number };
const base = (enterprise: boolean) => enterprise ? '/tenant-admin/billing/model-orders' : '/me/billing/model-orders';
export const modelOrdersApi = {
  list: (enterprise: boolean, page: number) => api.get<ModelOrderPage>(base(enterprise), { params: { page, page_size: 20 } }).then(response => response.data),
  detail: (enterprise: boolean, id: string) => api.get<ModelOrder>(`${base(enterprise)}/${encodeURIComponent(id)}`).then(response => response.data),
};
