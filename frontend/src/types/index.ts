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

export interface CampaignBrief {
  goal?: string; audience?: string; pain_point?: string; test_dimensions?: string[];
  fixed_variables?: string; version_count?: number; reference_name?: string; reference_notes?: string;
}

export interface MaterialAuditItem {
  segment: string; requirement: string; status: 'available' | 'reshoot'; matched_assets: string[];
}

export interface VideoVariant {
  id: string; hook: string; selling_point: string; evidence: string; duration: number;
  status: 'draft' | 'preview_ready' | 'approved' | 'rejected'; review_note: string;
  metrics: { impressions?: number; spend?: number; three_second_rate?: number; completion_rate?: number; ctr?: number; cvr?: number };
}

export interface VideoProject {
  id: number; product_id: number; name: string; platform: string; duration_seconds: number;
  style: string; video_type: string; content_focus: string; presenter_mode: string; voice: string; selling_points: string;
  extra_requirements: string; status: string; output_url: string; production_mode: 'quick' | 'performance'; campaign_brief: CampaignBrief;
  plan: { title?: string; concept?: string; script?: string; compliance_note?: string; demo_notice?: string; shots?: VideoShot[];
    production_mode?: 'quick' | 'performance'; campaign_brief?: CampaignBrief;
    reference_analysis?: { source: string; summary: string; structure: string[] };
    material_audit?: MaterialAuditItem[];
    reshoot_list?: Array<{ title:string; segment:string; shot:string; duration:string; proof_goal:string }>;
    creative_matrix?: VideoVariant[];
    quality_checks?: Array<{ label:string; status:'passed'|'review'|'blocked' }>;
    next_round_advice?: string[];
  };
  product: { name: string; brand_name: string; category: string; image_urls: string[] };
  created_at: string; updated_at: string;
}

export interface AutoEditClip {
  clip_id:string; order:number; asset_id:string; highlight_id:string;
  source_range:{start:number;end:number}; timeline_range:{start:number;end:number};
  duration_seconds:number; role:string; reason:string; audio_mode:'source'|'mute'; transition:'cut'|'fade';
}
export type VideoReusableStatus = 'review_required'|'reusable'|'excluded'|'blocked';
export type VideoObjectType = 'material'|'final_output';
export type VideoTagAppliesTo = VideoObjectType|'both';
export interface VideoCategoryDefinition {
  id:number;object_type:VideoObjectType;parent_id:number|null;name:string;code:string;sort_order:number;
  status:'active'|'inactive';usage_count:number;child_count:number;
}
export interface VideoTagGroupDefinition {
  id:number;name:string;code:string;applies_to:VideoTagAppliesTo;inherit_to_final:boolean;sort_order:number;
  status:'active'|'inactive';usage_count:number;tag_count:number;
}
export interface VideoTaxonomyTag {
  id:number;group_id:number|null;name:string;code:string;scope:'public'|'personal';applies_to:VideoTagAppliesTo;
  sort_order:number;status:'active'|'inactive';usage_count:number;
}
export interface VideoTagAssignment extends Omit<VideoTaxonomyTag,'code'|'sort_order'|'status'|'usage_count'> {
  group_name:string|null;status:'active'|'inactive';source:'manual'|'inherited'|'ai';confidence:number;confirmed:boolean;
}
export interface VideoFilterPreset {
  id:number;object_type:VideoObjectType;name:string;filters:Record<string,unknown>;created_at:string;updated_at:string;
}
export interface VideoTaxonomyCatalog {
  object_type:VideoObjectType;can_manage_public:boolean;categories:VideoCategoryDefinition[];
  groups:VideoTagGroupDefinition[];public_tags:VideoTaxonomyTag[];personal_tags:VideoTaxonomyTag[];presets:VideoFilterPreset[];
}
export interface VideoTaxonomyFilterValue {
  categoryId:number|null;publicTagIds:number[];publicTagMatchMode:'any'|'all';personalTagIds:number[];
}
export interface AutoEditVariant {
  variant_id:string; name:string; target_duration_seconds:number; actual_duration_seconds:number; edit_mode?:'smart_compose'|'finished_remix';
  strategy:{hook_approach:string;selection_principle:string;pace:string;average_shot_duration_seconds:number;difference_from_other_versions:string};
  speaker_count:number;
  narrative_segments:Array<{segment_id:string;order:number;asset_id:string;highlight_id:string;speaker_id:string;source_range:{start:number;end:number};timeline_range:{start:number;end:number};duration_seconds:number;content_role:string;speech_text:string;reason:string;audio_strategy:'keep_original'|'ai_voiceover';quality_score?:number;role_match_score?:number;previous_transition_score?:number;next_transition_score?:number;voice_continuity_score?:number;transition_reason?:string}>;
  video_track:Array<{clip_id:string;asset_id:string;highlight_id:string;source_range:{start:number;end:number};timeline_range:{start:number;end:number};duration_seconds:number;visual_type:'speaker'|'b_roll'|'source_sync';audio_strategy:'mute'|'keep_original';content_role:string}>;
}
export interface AutoEditAsset {
  id:number; asset_id:string; filename:string; object_url:string; duration_seconds:number; width:number;height:number;fps:number;has_audio:boolean;status:string;
  analysis:{summary:string;analysis_source:string;usability?:{score:number;summary:string;issues:string[]};burned_subtitles?:boolean;has_background_music?:boolean}|null;
  highlights:Array<{highlight_id:string;start:number;end:number;description:string;reason:string;recommended_roles:string[];content_role:string;track_role:string;speech_text:string;speaker_id:string;audio_strategy:string;shot_type:string;score:number}>;
}
export interface VideoLibraryAsset {
  id:number;asset_id:string;product_id:number;filename:string;source:string;library_type:'semi_finished'|'finished';asset_kind:'material'|'final_output';generation_depth:number;origin_asset_ids:string[];reusable_status:VideoReusableStatus;burned_subtitles:boolean;has_background_music:boolean;object_url:string;duration_seconds:number;width:number;height:number;fps:number;has_audio:boolean;status:string;excluded:boolean;duplicate?:boolean;
  category_id:number|null;category:null|{id:number;name:string;code:string;status:'active'|'inactive';parent_id:number|null;parent_name:string|null;path:string[]};
  created_at:string;
  ai_tags:string[];manual_tags:string[];removed_ai_tags:string[];effective_tags:string[];
  taxonomy_tags:VideoTagAssignment[];public_tags:VideoTagAssignment[];personal_tags:VideoTagAssignment[];
  analysis:null|{id:number;status:string;summary:string;model:string;prompt_version:string;analysis_json:Record<string,unknown>;request_count:number;retry_count:number;error_message:string};
  highlights:Array<{highlight_id:string;start:number;end:number;description:string;reason:string;recommended_roles:string[];content_role?:string;track_role?:string;speech_text?:string;speaker_id?:string;score:number}>;
}
export interface AutoEditProject {
  id:number;product_id:number;name:string;target_duration_seconds:number;variant_count:number;aspect_ratio:string;objective:string;edit_mode:'smart_compose'|'finished_remix'|'manual_compose';
  structure:Array<{id?:string;role:string;title?:string;objective:string;required:boolean;asset_ids?:string[]}>;constraints:string[];status:string;error_message:string;
  product:{name:string;image_urls:string[]};assets:AutoEditAsset[];plan_version_id:number|null;
  material_readiness:{original_speech_segments:number;supporting_visual_segments:number;original_speech_seconds:number;speaker_count:number;ready:boolean;missing:string[]};
  plan:{schema_version:string;material_selection?:{a_roll_count:number;b_roll_count:number;a_roll_seconds:number;notes:string};variants:AutoEditVariant[]}|null;
  outputs:Array<{variant_id:string;status:string;output_url:string;duration_seconds:number;error_message:string}>;
}

export interface DirectorAsset {
  id: number;
  section_id: number;
  role: 'reference' | 'material';
  media_kind: 'image' | 'video';
  source: 'uploaded' | 'generated' | 'extracted' | 'collected';
  url: string;
  thumbnail_url: string;
  description: string;
  duration_seconds: number;
  sort_order: number;
}

export interface DirectorScriptLine {
  id: string;
  text: string;
}

export interface DirectorShot {
  id: number;
  section_id: number;
  sort_order: number;
  start_ms: number;
  end_ms: number;
  transcript: string;
  story_role: string;
  subject: string;
  action: string;
  scene: string;
  shot_size: string;
  camera: string;
  product: string;
  props: string;
  emotion: string;
  director_intent: string;
  description: string;
  clip_url: string;
  thumbnail_url: string;
  confidence: number;
  analysis: { evidence?: string[]; product?: string; props?: string; emotion?: string; director_intent?: string; keyframe_count?: number };
}

export interface DirectorSection {
  id: number;
  sort_order: number;
  title: string;
  start_ms: number;
  end_ms: number;
  source_text: string;
  script_lines: DirectorScriptLine[];
  description: string;
  shooting_notes: string;
  assets: DirectorAsset[];
  shots: DirectorShot[];
}

export interface DirectorProject {
  id: number;
  product_id: number | null;
  name: string;
  mode: 'reference' | 'original';
  source_kind: 'upload' | 'url' | 'prompt' | 'browser_extension';
  source_url: string;
  source_filename: string;
  source_duration_seconds: number;
  language: string;
  product_service: string;
  selling_points: string;
  status: string;
  progress_stage: string;
  analysis: {
    summary?: string;
    tags?: string[];
    product_name?: string;
    target_audience?: string | string[];
    target_audience_detail?: string | string[];
    target_pain_points?: string | string[];
    selling_points?: string[];
    selling_points_detail?: string | string[];
    language?: string;
    frame_count?: number;
    shot_count?: number;
    section_count?: number;
    audio_analyzed?: boolean;
    media?: {
      probe_provider?: string;
      width?: number;
      height?: number;
      fps?: number;
      video_codec?: string;
      has_audio?: boolean | null;
      audio_track_count?: number;
      audio_codec?: string;
    };
    asr?: { provider?: string; model?: string; status?: string; segment_count?: number };
    warnings?: string[];
    hook?: { content?: string; shot_indexes?: number[] };
    selling_point_chain?: Array<{ stage?: string; content?: string; shot_indexes?: number[] }>;
    rhythm?: { summary?: string; average_shot_seconds?: number; fast_sections?: string[]; slow_sections?: string[] };
    cta?: { type?: string; content?: string; shot_indexes?: number[] };
    analysis_engine?: string;
    capture?: {
      source_page_url?: string;
      source_platform?: string;
      content_type?: string;
      filename?: string;
      size_bytes?: number;
    };
  };
  output_url: string;
  error_message: string;
  product: { id: number; name: string; brand_name: string; category: string; image_urls: string[] } | null;
  sections: DirectorSection[];
  created_at: string;
  updated_at: string;
  collected_count?: number;
}

export interface ShootingDirectory {
  id: string;
  name: string;
  departments: Array<{
    id: string;
    name: string;
    members: Array<{ id: string; name: string }>;
  }>;
}

export interface ShootingTask {
  id: number;
  project_id: number;
  project_name: string;
  project_mode: 'reference' | 'original';
  section_count: number;
  channel_id: string;
  channel_name: string;
  department_id: string;
  department_name: string;
  assignee_id: string;
  assignee_name: string;
  note: string;
  status: 'pending' | 'in_progress' | 'completed';
  created_at: string;
  updated_at: string;
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
