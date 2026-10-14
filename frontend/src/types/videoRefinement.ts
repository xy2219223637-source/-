import type {VideoLibraryAsset} from './index';

export type RefinementFocus = 'hook' | 'pace' | 'product_visibility' | 'voice' | 'subtitle';
export type RefinementItemType = 'VISUAL_REPLACE' | 'PACE_RECUT' | 'SEGMENT_INSERT' | 'VOICE_REPLACE' | 'SUBTITLE_EDIT';
export interface RefinementSource {
  id: number; product_id: number; product_name: string; filename: string; object_url: string;
  duration_seconds: number; width: number; height: number; fps: number; has_audio: boolean;
  burned_subtitles: boolean; created_at: string;
  category_id?: VideoLibraryAsset['category_id']; category?: VideoLibraryAsset['category'];
  taxonomy_tags?: VideoLibraryAsset['taxonomy_tags']; public_tags?: VideoLibraryAsset['public_tags']; personal_tags?: VideoLibraryAsset['personal_tags'];
}
export interface RefinementSnapshot {
  schema_version: 'video_refinement_snapshot.v1' | 'video_refinement_snapshot.v2'; is_mock: boolean; fixture: string;
  source: { asset_id: number; product_id: number; product_name: string; filename: string; object_url: string;
    version: string; sku: string | null; content_hash: string; duration_ms: number; width: number; height: number;
    aspect_ratio: string; fps: number; frame_rate?: string; time_base?: string; video_duration?: string; has_audio: boolean; burned_subtitles: boolean; has_background_music: boolean };
  timeline: Array<{ shot_id: string; start_ms: number; end_ms: number; label: string; is_mock: boolean }>;
  transcript: Array<{ cue_id: string; start_ms: number; end_ms: number; text: string; is_mock: boolean }>;
  subtitles: Array<{ cue_id: string; start_ms: number; end_ms: number; text: string; is_mock: boolean }>;
  material_catalog: Array<{ asset_id: number; product_id: number; filename: string; object_url: string; content_hash: string; duration_ms: number; is_mock_mapping: boolean; analysis_status?: string }>;
  material_scope?: {total: number; complete: boolean};
  refresh_changes?: {added: number; removed: number; changed: number};
  words?: Array<{start_ms: number; end_ms: number; text: string}>;
  asr_state?: string;
  capabilities: { visual: Capability; pace: Capability; voice: Capability; subtitles: Capability; aspect_ratio_locked: boolean; sku_verified: boolean };
}
interface Capability { state: 'PLAN_ONLY' | 'HUMAN_REQUIRED' | 'EXECUTABLE'; reason: string }
export interface VideoRefinementPlanItem {
  item_id: string; type: RefinementItemType; range: { start_ms: number; end_ms: number }; operation: string;
  reason: string; evidence: string[]; confidence_level: 'LOW' | 'MEDIUM' | 'HIGH'; enabled: boolean;
  duration_delta_ms: number; material_asset_id: number | null; script: string; subtitle_text: string;
  material_start_ms?: number; audio_strategy?: 'keep_source' | 'use_material'; cut_from?: 'start' | 'end';
  edit_intent?: 'replace_visual' | 'reduce_shot_share' | 'insert_content' | 'remove_pause' | 'remove_content' | 'compress_expression';
  edit_group_id?: string | null; requirement_ids?: string[]; material_segments?: MaterialSegment[]; material_audio_policy?: 'mute';
  narration_strategy?: 'keep_source' | 'cut_source' | 'tts' | 'none'; removed_ranges?: TimeRange[]; affected_cue_ids?: string[]; voiceover_id?: number | null;
  tts_speaker?: string | null;
  subtitle_strategy?: 'keep_visible' | 'remap' | 'regenerate' | 'none' | 'human_required'; background_strategy?: string; boundary_reviewed?: boolean; narration_padding_reason?: string;
  risk: string; dependencies: string[]; status: 'PROPOSED' | 'HUMAN_EDITED' | 'CONFIRMED';
}
export interface VideoRefinementPlan {
  automatic_preview?: boolean;
  timing_version?: string;
  automatic_adjustments?: Array<{item_id: string; asset_id: number; code: string; before_range: TimeRange; after_range: TimeRange; removed_frames: number; message: string}>;
  schema_version: 'video_refinement_plan.v1' | 'video_refinement_plan.v2' | 'video_refinement_plan.v3'; source: RefinementSnapshot['source'];
  summary: { objective: string; expected_result: string; expected_duration_ms: number; duration_delta_ms: number;
    duration_reason: string; confidence_level: string; is_mock: boolean; total_frames?: number; sample_count?: number; sample_rate?: number; frame_rate?: string };
  local_selection?: Array<{item_id: string; asset_id: number; candidate_id: string; source_range: TimeRange; safe_range: TimeRange; preferred_range: TimeRange; reason: string; verification: {method: string; sampled_times_ms: number[]; risks: string[]}; product_match: {sku_verified: boolean; evidence: string}}>;
  items: VideoRefinementPlanItem[];
  execution_blockers: RefinementBlocker[];
  requirements_resolution?: RefinementRequirement[]; material_evaluation?: MaterialEvaluation;
  video_track?: RefinementTrack[]; narration_track?: RefinementTrack[]; source_to_output_map?: Array<{kind: string; source_range: TimeRange; output_range: TimeRange; item_ids: string[]}>;
  findings_not_applied: string[];
  analysis?: {model: string; mode: 'full_video' | 'sampled_frames'; timeline: RefinementSnapshot['timeline']; source_hash: string; voice_selection?: RefinementVoiceSelection};
}
export interface VideoRefinementPreview {
  id: number; revision: number; plan_hash: string; status: 'queued' | 'rendering' | 'ready' | 'failed' | 'superseded' | 'accepted';
  output_url: string; error_message: string;
  validation: {passed?: boolean; duration_seconds?: number; width?: number; height?: number; decode_checked?: boolean};
}
export interface VideoRefinementTask {
  id: number; source_asset_id: number; product_id: number; source_hash: string; source_snapshot: RefinementSnapshot;
  snapshot_hash: string; user_brief: string; expected_result: string; focus: RefinementFocus[];
  status: 'CREATED' | 'PLAN_READY' | 'CONFIGURING' | 'NEEDS_HUMAN_CONFIRMATION' | 'MATERIAL_REQUIRED' | 'PLAN_CONFIRMED' | 'CANCELLED' | 'COMPLETED';
  current_revision: number; confirmed_revision: number | null; plan: VideoRefinementPlan | null; plan_hash: string | null;
  options?: {duration_preference?: DurationPreference; target_duration_ms?: number | null; tts_speaker?: string | null};
  analysis_state?: {id: number; revision: number; status: string; progress: MaterialEvaluation; error_message: string; created_at?: string; started_at?: string | null; queue_wait_seconds?: number} | null;
  worker_state?: {status: 'running' | 'disabled' | 'stopped'; message: string};
  voiceovers?: RefinementVoiceover[];
  preview_supported: boolean; created_at: string; updated_at: string;
  previews?: VideoRefinementPreview[]; output_asset_id?: number | null; output_version?: number | null;
}

export type DurationPreference = 'preserve' | 'shorten' | 'ai_recommend';
export interface TimeRange {start_ms: number; end_ms: number}
export interface MaterialSegment {asset_id: number; source_range: TimeRange; candidate_id?: string | null}
export interface RefinementRequirement {
  requirement_id: string; text: string; request_quote?: string; reason: string; question: string; item_ids: string[];
  status: 'EXECUTABLE' | 'ALREADY_SATISFIED' | 'NEEDS_CLARIFICATION' | 'CONFLICT' | 'UNSUPPORTED' | 'AWAITING_MATERIAL';
  planning_status?: RefinementRequirement['status']; planning_reason?: string; conflict_ids?: string[];
  decision: 'pending' | 'waived' | 'answered'; answer: string;
  execution_blockers?: RefinementBlocker[];
}
export interface RefinementBlocker {item_id: string; code: string; message: string; category?: 'material' | 'audio' | 'input' | 'technical' | 'content'}
export interface MaterialEvaluation {total?: number; available?: number; excluded?: number; reused_analysis?: number; new_analysis?: number; analyzed?: number; partial_accepted?: boolean; evaluated?: number; pending?: number; failed?: number; visual_verified?: number; complete?: boolean; stage?: string}
export interface RefinementTrack {source_kind: string; source_asset_id: number; source_range: TimeRange; output_range: TimeRange; item_ids: string[]; output_frames?: {start_frame: number; end_frame: number}; output_samples?: {start_sample: number; end_sample: number}}
export interface RefinementVoiceSelection {speaker: string; name: string; speech_rate: number; mode: 'auto' | 'manual'; reason: string; basis?: 'source_audio_and_content' | 'content_only'}
export interface RefinementSpeaker {id: string; name: string; gender: 'female' | 'male'; languages: string[]; resource_id: string}
export interface RefinementSpeakerCatalog {version: string; total: number; items: RefinementSpeaker[]; default_mode: 'auto'}
export interface RefinementVoiceover {id: number; item_id: string; revision: number; script: string; fingerprint: string; status: string; duration_ms: number; sample_count?: number | null; sample_rate?: number | null; object_url: string; error_message: string; parameters: {speaker: string; selection?: RefinementVoiceSelection}; timing: {segments?: Array<{start: number; end: number; text: string}>}}
export interface RefinementMaterialPage {items: RefinementSnapshot['material_catalog']; total: number; next_cursor: string | null; snapshot_hash: string}
