export interface Product {
  id: number;
  name: string;
  category: string;
  price: number;
  description: string;
  target_users: string;
  brand_name: string;
  ingredients: string;
  usage_method: string;
  specifications: string;
  image_urls: string[];
  detail_image_urls: string[];
  created_at: string;
  updated_at: string;
  learned_profile_enabled: boolean;
}

export interface ProductCreate {
  name: string;
  category: string;
  price: number;
  description: string;
  target_users: string;
  brand_name: string;
  ingredients: string;
  usage_method: string;
  specifications: string;
  image_urls: string[];
  detail_image_urls: string[];
  learned_profile_enabled?: boolean;
}

export interface VideoShot {
  id: string; duration: number; type: string; title: string;
  visual: string; voiceover: string; status: 'planned' | 'generating' | 'ready' | 'failed';
}

export interface VideoProject {
  id: number; product_id: number; name: string; platform: string; duration_seconds: number;
  style: string; video_type: string; content_focus: string; presenter_mode: string; voice: string; selling_points: string;
  extra_requirements: string; status: string; output_url: string;
  plan: { title?: string; concept?: string; script?: string; compliance_note?: string; demo_notice?: string; shots?: VideoShot[] };
  product: { name: string; brand_name: string; category: string; image_urls: string[] };
  created_at: string; updated_at: string;
}

export interface StoryboardModule {
  id: number;
  project_id: number;
  sort_order: number;
  module_type: string;
  title: string;
  objective: string;
  content_guidance: string;
  visual_direction: string;
  production_method: string;
  required: boolean;
  status: string;
  preview_node_id: string | null;
  final_node_id: string | null;
  created_at: string;
  updated_at: string;
}

export interface CreativePlan {
  id: number;
  project_id: number;
  product_understanding: Record<string, unknown>;
  strategy: Record<string, unknown>;
  status: string;
  modules: StoryboardModule[];
}

export interface StoryboardBatchJob {
  id: string;
  project_id: number;
  status: string;
  module_ids: number[];
  module_results: Array<{ module_id: number; status: string; error?: string | { code?: string; title: string; message?: string; suggestion?: string; retryable?: boolean } }>;
  total: number;
  completed: number;
  failed: number;
  current_module_id: number | null;
  stop_requested: boolean;
}

export interface ComplianceReport {
  status: 'passed' | 'review' | 'blocked';
  score: number;
  high_count: number;
  medium_count: number;
  issues: Array<{ module_id: number; module_title: string; severity: 'high' | 'medium'; type: string; claim: string; message: string; sources: Array<{ id: number; title: string; doc_type: string }> }>;
  knowledge_sources: Array<{ id: number; title: string; doc_type: string }>;
  visual_quality?: { status: 'passed' | 'review' | 'blocked'; score: number; checked_count: number; high_count: number; medium_count: number; issues: Array<{ module_id: number; module_title: string; severity: 'high' | 'medium'; type: string; message: string }> };
  product_consistency?: { status: 'passed' | 'review' | 'blocked' | 'unavailable'; score?: number; checked_count: number; summary?: string; message?: string; issues: Array<{ output_index: number; severity: 'high' | 'medium'; field: string; message: string; confidence: number }> };
}

export interface KnowledgeDoc {
  id: number;
  product_id: number | null;
  brand_name: string;
  title: string;
  doc_type: string;
  filename: string | null;
  content: string;
  chunk_count: number;
  created_at: string;
}

export interface BrandVisualProfile {
  id?: number;
  brand_name: string;
  logo_url: string;
  primary_color: string;
  accent_color: string;
  typography: string;
  visual_keywords: string[];
  forbidden_elements: string[];
  tone_notes: string;
}

export interface DesignSkill {
  id: number;
  name: string;
  scope: 'general' | 'category' | 'brand' | 'product';
  category: string;
  brand_name: string;
  product_id: number | null;
  description: string;
  design_principles: string;
  module_guidance: string;
  visual_rules: string;
  copy_rules: string;
  negative_rules: string;
  primary_color: string;
  accent_color: string;
  enabled: boolean;
  version: number;
  created_at: string;
  updated_at: string;
}

export type DesignSkillCreate = Omit<DesignSkill, 'id' | 'version' | 'created_at' | 'updated_at'>;

export interface LearnedDesignProfile {
  id: number;
  brand_name: string;
  category: string;
  sample_count: number;
  positive_count: number;
  negative_count: number;
  confidence: number;
  learned_rules: Record<string, Array<{ value: string; count: number }> | string>;
  status: 'observing' | 'stable';
  updated_at: string;
}

export interface SkillCandidate {
  id: number; profile_id: number; name: string; brand_name: string; category: string;
  confidence: number; sample_count: number; payload: DesignSkillCreate;
  status: 'pending' | 'published' | 'rejected'; published_skill_id: number | null;
  created_at: string; updated_at: string;
}

export interface DetailPageTemplate {
  id: number; name: string; description: string; category: string; brand_name: string;
  platform: string; output_width: number; output_height: number; source_project_id: number | null;
  modules: Array<Record<string, unknown>>; usage_count: number; enabled: boolean;
  variables: Array<{key:string;label:string;required:boolean}>; conditions: Record<string,string>;
  created_at: string; updated_at: string;
}

export interface CreativeProject {
  id: number;
  product_id: number;
  name: string;
  brief: string;
  platform: string;
  output_width: number;
  output_height: number;
  status: string;
  review_status: string;
  review_round: number;
  viewport: { x?: number; y?: number; zoom?: number };
  created_at: string;
  updated_at: string;
}

export interface CanvasNodeRecord {
  id: string;
  project_id: number;
  node_type: string;
  parent_node_id: string | null;
  position_x: number;
  position_y: number;
  width: number;
  height: number;
  data: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface CreativeFeedback {
  id: number;
  project_id: number;
  product_id: number;
  node_id: string;
  image_url: string;
  status: string;
  reasons: string[];
  weight: number;
  learning_status: string;
}

export interface ProductAsset {
  id: number;
  product_id: number | null;
  name: string;
  asset_type: string;
  file_url: string;
  mime_type: string;
  description: string;
  tags: string[];
  material_role: string;
  priority: number;
  locked: boolean;
  excluded: boolean;
  benchmark_role: string;
  protection: {mask_url?:string;mask_source?:string;protected_regions?:Array<{type:string;text?:string;x:number;y:number;width:number;height:number;confidence?:number}>;position?:{x:number;y:number;scale:number;rotation:number};preserve_shadow?:boolean;preserve_reflection?:boolean};
  created_at: string;
}

export interface DashboardProjectItem {
  id: number;
  name: string;
  product_name: string;
  platform: string;
  status: string;
  review_status: string;
  completed_pages: number;
  total_pages: number;
  updated_at: string;
  path: string;
}

export interface DashboardStats {
  summary: {
    project_count: number;
    in_progress_projects: number;
    completed_pages: number;
    failed_tasks: number;
    generated_images: number;
  };
  recent_projects: DashboardProjectItem[];
  recent_project_tasks: Array<{
    project_id: number;
    project_name: string;
    status: string;
    running: number;
    pending: number;
    completed: number;
    failed: number;
    generated_images: number;
    created_at: string;
    updated_at: string;
    triggered_by: string;
    trigger_source: string;
    path: string;
  }>;
  todos: Array<{
    project_id: number;
    project_name: string;
    title: string;
    action_label: string;
    kind: string;
    path: string;
    updated_at: string;
  }>;
  last_project: DashboardProjectItem | null;
}

export interface CreativeGenerationRecord {
  id: number;
  module_id: number | null;
  module_title: string;
  action: string;
  status: string;
  provider: string;
  result_count: number;
  triggered_by: string;
  trigger_source: string;
  error_message: string;
  suggestion: string;
  created_at: string;
}

export const SECTION_LABELS: Record<string, string> = {
  title: '商品标题',
  selling_points: '核心卖点',
  advantages: '产品优势',
  scenarios: '使用场景',
  pain_solutions: '痛点解决方案',
  purchase_reasons: '购买理由',
  faq: 'FAQ',
  after_sales: '售后说明',
};
