import axios from 'axios';
import type {
  DashboardStats,
  DesignSkill,
  DesignSkillCreate,
  LearnedDesignProfile,
  CreativeProject,
  CreativeGenerationRecord,
  CreativePlan,
  CanvasNodeRecord,
  CreativeFeedback,
  KnowledgeDoc,
  Product,
  ProductAsset,
  ProductCreate,
  StoryboardBatchJob,
  BrandVisualProfile,
  ComplianceReport,
  SkillCandidate,
  DetailPageTemplate,
} from '../types';

const api = axios.create({
  baseURL: '/api',
  timeout: 120000,
});

api.interceptors.request.use((config) => {
  const token = localStorage.getItem('access_token');
  if (token) config.headers.Authorization = `Bearer ${token}`;
  return config;
});

api.interceptors.response.use(undefined, (error) => {
  if (error.response?.status === 401 && !window.location.pathname.startsWith('/login')) {
    localStorage.removeItem('access_token');
    window.location.href = '/login';
  }
  return Promise.reject(error);
});

export const authApi = {
  login: (email: string, password: string) => api.post('/auth/login', { email, password }).then((r) => r.data),
  register: (payload: { tenant_name: string; tenant_code: string; name: string; email: string; password: string }) =>
    api.post('/auth/register', payload).then((r) => r.data),
};

export function mediaUrl(path: string): string {
  if (!path) return '';
  if (path.startsWith('http://') || path.startsWith('https://') || path.startsWith('data:')) {
    return path;
  }
  return path;
}

export function apiUrl(path: string): string {
  return `/api${path}`;
}

let productListCache: { data: Product[]; expiresAt: number } | null = null;
let productListRequest: Promise<Product[]> | null = null;

function invalidateProductListCache() {
  productListCache = null;
}

function listProducts() {
  if (productListCache && productListCache.expiresAt > Date.now()) return Promise.resolve(productListCache.data);
  if (productListRequest) return productListRequest;
  productListRequest = api.get<Product[]>('/products').then((response) => {
    productListCache = { data: response.data, expiresAt: Date.now() + 30_000 };
    return response.data;
  }).finally(() => { productListRequest = null; });
  return productListRequest;
}

export const productApi = {
  list: listProducts,
  get: (id: number) => api.get<Product>(`/products/${id}`).then((r) => r.data),
  create: (data: ProductCreate) => api.post<Product>('/products', data).then((r) => { invalidateProductListCache(); return r.data; }),
  update: (id: number, data: Partial<ProductCreate>) =>
    api.put<Product>(`/products/${id}`, data).then((r) => { invalidateProductListCache(); return r.data; }),
  remove: (id: number) => api.delete(`/products/${id}`).then((r) => { invalidateProductListCache(); return r.data; }),
  uploadImage: async (file: File) => {
    const form = new FormData();
    form.append('file', file);
    const { data } = await api.post<{ url: string }>('/products/upload-image', form);
    return data.url;
  },
  uploadProductImages: async (productId: number, files: File[], imageType: 'product' | 'detail') => {
    const form = new FormData();
    form.append('image_type', imageType);
    files.forEach((f) => form.append('files', f));
    const { data } = await api.post<Product>(`/products/${productId}/images`, form);
    invalidateProductListCache();
    return data;
  },
  listAssets: (productId: number) =>
    api.get<ProductAsset[]>(`/products/${productId}/assets`).then((r) => r.data),
  uploadAssets: async (
    productId: number,
    files: File[],
    meta: { asset_type: string; description?: string; tags?: string },
  ) => {
    const form = new FormData();
    form.append('asset_type', meta.asset_type);
    form.append('description', meta.description || '');
    form.append('tags', meta.tags || '');
    files.forEach((file) => form.append('files', file));
    const { data } = await api.post<ProductAsset[]>(`/products/${productId}/assets`, form);
    return data;
  },
  removeAsset: (productId: number, assetId: number) =>
    api.delete(`/products/${productId}/assets/${assetId}`).then((r) => r.data),
  updateAsset: (productId: number, assetId: number, data: Pick<ProductAsset, 'description' | 'tags' | 'material_role' | 'priority' | 'locked' | 'excluded' | 'benchmark_role' | 'protection'>) =>
    api.put<ProductAsset>(`/products/${productId}/assets/${assetId}`, data).then((r) => r.data),
  autoMask:(productId:number,assetId:number)=>api.post<ProductAsset>(`/products/${productId}/assets/${assetId}/auto-mask`).then(r=>r.data),
  analyzeProtection:(productId:number,assetId:number)=>api.post<ProductAsset>(`/products/${productId}/assets/${assetId}/analyze-protection`,undefined,{timeout:180000}).then(r=>r.data),
  uploadMask:async(productId:number,assetId:number,file:File)=>{const form=new FormData();form.append('file',file);return api.post<ProductAsset>(`/products/${productId}/assets/${assetId}/mask`,form).then(r=>r.data)},
};

export const videoApi = {
  list: () => api.get<import('../types').VideoProject[]>('/video-projects').then(r => r.data),
  get: (id: number) => api.get<import('../types').VideoProject>(`/video-projects/${id}`).then(r => r.data),
  create: (data: { product_id:number; duration_seconds:number; platform:string; video_type:string; voice:string; selling_points:string; extra_requirements:string; production_mode?:'quick'|'performance'; campaign_brief?:import('../types').CampaignBrief }) => api.post<import('../types').VideoProject>('/video-projects', data).then(r => r.data),
  generatePlan: (id: number) => api.post<import('../types').VideoProject>(`/video-projects/${id}/plan`, undefined, { timeout: 180000 }).then(r => r.data),
  updatePlan: (id: number, plan: Record<string, unknown>) => api.put<import('../types').VideoProject>(`/video-projects/${id}/plan`, { plan }).then(r => r.data),
  generate: (id: number) => api.post<import('../types').VideoProject>(`/video-projects/${id}/generate`).then(r => r.data),
  regenerateShot: (id: number, shotId: string) => api.post<import('../types').VideoProject>(`/video-projects/${id}/shots/${encodeURIComponent(shotId)}/regenerate`).then(r => r.data),
  batchGenerate: (id: number) => api.post<import('../types').VideoProject>(`/video-projects/${id}/batch-generate`).then(r => r.data),
  reviewVariant: (id:number, variantId:string, data:{status:'draft'|'approved'|'rejected';note?:string}) => api.put<import('../types').VideoProject>(`/video-projects/${id}/variants/${encodeURIComponent(variantId)}/review`, data).then(r=>r.data),
  updatePerformance: (id:number, variantId:string, data:{impressions:number;spend:number;three_second_rate:number;completion_rate:number;ctr:number;cvr:number}) => api.put<import('../types').VideoProject>(`/video-projects/${id}/variants/${encodeURIComponent(variantId)}/performance`, data).then(r=>r.data),
  uploadReference: async (id:number,file:File) => { const form=new FormData(); form.append('file',file); return api.post<import('../types').VideoProject>(`/video-projects/${id}/reference-video`,form,{timeout:180000}).then(r=>r.data); },
};

export const autoEditApi = {
  list: (editMode?:'smart_compose'|'finished_remix') => api.get<import('../types').AutoEditProject[]>('/auto-edit-projects',{params:editMode?{edit_mode:editMode}:undefined}).then(r=>r.data),
  get: (id:number) => api.get<import('../types').AutoEditProject>(`/auto-edit-projects/${id}`).then(r=>r.data),
  create: (data:{product_id:number;name?:string;target_duration_seconds:number;variant_count:number;aspect_ratio:string;objective:string;edit_mode:'smart_compose'|'finished_remix';constraints?:string[]}) =>
    api.post<import('../types').AutoEditProject>('/auto-edit-projects',data).then(r=>r.data),
  updateDuration: (id:number,target_duration_seconds:number) =>
    api.patch<import('../types').AutoEditProject>(`/auto-edit-projects/${id}/duration`,{target_duration_seconds}).then(r=>r.data),
  uploadAssets: async (id:number,files:File[]) => { const form=new FormData(); files.forEach(file=>form.append('files',file)); return api.post<import('../types').AutoEditProject>(`/auto-edit-projects/${id}/assets`,form,{timeout:600000}).then(r=>r.data); },
  generatePlan: (id:number) => api.post<import('../types').AutoEditProject>(`/auto-edit-projects/${id}/plan`,undefined,{timeout:180000}).then(r=>r.data),
  render: (id:number) => api.post<import('../types').AutoEditProject>(`/auto-edit-projects/${id}/render`,undefined,{timeout:1200000}).then(r=>r.data),
};

export const videoLibraryApi = {
  list: (productId?:number,libraryType?:'semi_finished'|'finished',assetKind:'material'|'final_output'='material') => api.get<import('../types').VideoLibraryAsset[]>('/video-assets',{params:{...(productId?{product_id:productId}:{}),...(libraryType?{library_type:libraryType}:{}),asset_kind:assetKind}}).then(r=>r.data),
  upload: async (productId:number,files:File[],source='manual_upload',onProgress?:(loaded:number,total:number,percent:number)=>void,libraryType:'semi_finished'|'finished'='semi_finished') => {
    const total=files.reduce((sum,file)=>sum+file.size,0);let completedBytes=0;const results:import('../types').VideoLibraryAsset[]=[];
    for(const file of files){
      const start=await api.post<{session_id:string;part_size:number;part_count:number;part_urls:string[]}>('/video-assets/multipart/start',{product_id:productId,filename:file.name,content_type:file.type||'application/octet-stream',size_bytes:file.size,source,library_type:libraryType}).then(r=>r.data);
      const progress=new Map<number,number>();
      const uploadPart=async(index:number)=>{const begin=index*start.part_size;const blob=file.slice(begin,Math.min(file.size,begin+start.part_size));let lastError:unknown;
        for(let attempt=0;attempt<3;attempt++)try{await axios.put(start.part_urls[index],blob,{timeout:180000,headers:{'Content-Type':'application/octet-stream'},onUploadProgress:event=>{progress.set(index,Math.min(event.loaded,blob.size));const loaded=completedBytes+Array.from(progress.values()).reduce((n,value)=>n+value,0);onProgress?.(loaded,total,total?Math.round(loaded/total*100):0)}});progress.set(index,blob.size);return}catch(error){lastError=error;progress.set(index,0)}
        throw lastError};
      for(let offset=0;offset<start.part_count;offset+=4)await Promise.all(Array.from({length:Math.min(4,start.part_count-offset)},(_,i)=>uploadPart(offset+i)));
      completedBytes+=file.size;onProgress?.(completedBytes,total,total?Math.round(completedBytes/total*100):0);
      results.push(await api.post<import('../types').VideoLibraryAsset>('/video-assets/multipart/complete',{session_id:start.session_id},{timeout:180000}).then(r=>r.data));
    }
    return results;
  },
  exclude: (id:number,excluded:boolean) => api.put<import('../types').VideoLibraryAsset>(`/video-assets/${id}/excluded`,{excluded}).then(r=>r.data),
  updateReusableStatus: (id:number,reusable_status:import('../types').VideoReusableStatus) =>
    api.put<import('../types').VideoLibraryAsset>(`/video-assets/${id}/reusable-status`,{reusable_status}).then(r=>r.data),
  updateTags: (id:number,data:{manual_tags:string[];removed_ai_tags:string[]}) => api.put<import('../types').VideoLibraryAsset>(`/video-assets/${id}/tags`,data).then(r=>r.data),
  reanalyze: (id:number) => api.post<import('../types').VideoLibraryAsset>(`/video-assets/${id}/reanalyze`).then(r=>r.data),
};

export const knowledgeApi = {
  list: (productId?: number) =>
    api
      .get<KnowledgeDoc[]>('/knowledge', {
        params: productId != null ? { product_id: productId } : undefined,
      })
      .then((r) => r.data),
  create: (data: {
    title: string;
    doc_type: string;
    content: string;
    product_id?: number | null;
    brand_name?: string;
  }) => api.post<KnowledgeDoc>('/knowledge', data).then((r) => r.data),
  upload: async (file: File, meta: { title?: string; doc_type: string; product_id?: number; brand_name?: string }) => {
    const form = new FormData();
    form.append('file', file);
    form.append('doc_type', meta.doc_type);
    if (meta.title) form.append('title', meta.title);
    if (meta.product_id != null) form.append('product_id', String(meta.product_id));
    if (meta.brand_name) form.append('brand_name', meta.brand_name);
    const { data } = await api.post<KnowledgeDoc>('/knowledge/upload', form);
    return data;
  },
  remove: (id: number) => api.delete(`/knowledge/${id}`).then((r) => r.data),
};

export const designSkillApi = {
  list: () => api.get<DesignSkill[]>('/design-skills').then((r) => r.data),
  create: (data: DesignSkillCreate) => api.post<DesignSkill>('/design-skills', data).then((r) => r.data),
  update: (id: number, data: DesignSkillCreate) => api.put<DesignSkill>(`/design-skills/${id}`, data).then((r) => r.data),
  remove: (id: number) => api.delete(`/design-skills/${id}`).then((r) => r.data),
  candidates: () => api.get<SkillCandidate[]>('/design-skills/candidates').then((r) => r.data),
  publishCandidate: (id: number) => api.post<DesignSkill>(`/design-skills/candidates/${id}/publish`).then((r) => r.data),
  rejectCandidate: (id: number) => api.post<SkillCandidate>(`/design-skills/candidates/${id}/reject`).then((r) => r.data),
  updateCandidate: (id: number, data: DesignSkillCreate) => api.put<SkillCandidate>(`/design-skills/candidates/${id}`, data).then((r) => r.data),
  versions: (id: number) => api.get<Array<{ id:number; version:number; snapshot:DesignSkillCreate; change_note:string; performance_snapshot:Record<string,unknown>; created_at:string; is_current:boolean }>>(`/design-skills/${id}/versions`).then((r) => r.data),
  rollback: (id: number, version: number) => api.post<DesignSkill>(`/design-skills/${id}/versions/${version}/rollback`).then((r) => r.data),
};

export const templateApi = {
  list: () => api.get<DetailPageTemplate[]>('/detail-page-templates').then((r) => r.data),
  save: (projectId: number, name: string, description = '') => api.post<DetailPageTemplate>('/detail-page-templates', { project_id: projectId, name, description }).then((r) => r.data),
  apply: (templateId: number, productId: number, projectName = '') => api.post<CreativeProject>(`/detail-page-templates/${templateId}/apply`, { product_id: productId, project_name: projectName }).then((r) => r.data),
  remove: (id: number) => api.delete(`/detail-page-templates/${id}`).then((r) => r.data),
};

export const brandVisualApi = {
  list: () => api.get<BrandVisualProfile[]>('/brand-visuals').then((r) => r.data),
  save: (brandName: string, data: BrandVisualProfile) => api.put<BrandVisualProfile>(`/brand-visuals/${encodeURIComponent(brandName)}`, data).then((r) => r.data),
  uploadLogo: async (brandName: string, file: File) => { const form = new FormData(); form.append('file', file); return api.post<BrandVisualProfile>(`/brand-visuals/${encodeURIComponent(brandName)}/logo`, form).then((r) => r.data); },
};

export const creativeApi = {
  list: () => api.get<CreativeProject[]>('/creative-projects').then((r) => r.data),
  get: (id: number) => api.get<CreativeProject>(`/creative-projects/${id}`).then((r) => r.data),
  create: (data: { product_id: number; name: string; brief: string; platform: string; output_width: number; output_height: number }) => api.post<CreativeProject>('/creative-projects', data).then((r) => r.data),
  nodes: (id: number) => api.get<CanvasNodeRecord[]>(`/creative-projects/${id}/nodes`).then((r) => r.data),
  saveCanvas: (id: number, nodes: Array<Partial<CanvasNodeRecord> & { id: string; node_type: string; position_x: number; position_y: number; data: Record<string, unknown> }>, viewport: Record<string, number>) => api.put<CanvasNodeRecord[]>(`/creative-projects/${id}/canvas`, { nodes, viewport }).then((r) => r.data),
  generate: (id: number, data: { prompt: string; action: string; selected_node_ids: string[]; parent_node_id?: string | null; auto_select_materials: boolean; module_id?: number; count: number; product_lock?: 'strict'|'balanced'|'creative'; variation_axis?: 'composition'|'scene'|'color'|'model'|'lighting'; generation_stage?: 'preview'|'final' }) => api.post<{ generation: { context_snapshot?: Record<string, unknown> }; nodes: CanvasNodeRecord[] }>(`/creative-projects/${id}/generate`, data, { timeout: 600000 }).then((r) => r.data),
  generations: (id: number) => api.get<CreativeGenerationRecord[]>(`/creative-projects/${id}/generations`).then((r) => r.data),
  retryGeneration: (projectId: number, generationId: number) => api.post(`/creative-projects/${projectId}/generations/${generationId}/retry`, undefined, { timeout: 600000 }).then((r) => r.data),
  getPlan: (id: number) => api.get<CreativePlan>(`/creative-projects/${id}/plan`).then((r) => r.data),
  generatePlan: (id: number) => api.post<CreativePlan>(`/creative-projects/${id}/plan`).then((r) => r.data),
  updatePlanModules: (id: number, modules: Array<Pick<CreativePlan['modules'][number], 'sort_order' | 'module_type' | 'title' | 'objective' | 'content_guidance' | 'visual_direction' | 'production_method' | 'required'>>) => api.put<CreativePlan>(`/creative-projects/${id}/plan/modules`, { modules }).then((r) => r.data),
  moduleVersions: (id: number, moduleId: number) => api.get<CanvasNodeRecord[]>(`/creative-projects/${id}/plan/modules/${moduleId}/versions`).then((r) => r.data),
  selectModuleVersion: (id: number, moduleId: number, nodeId: string, approve = false) => api.put<CreativePlan['modules'][number]>(`/creative-projects/${id}/plan/modules/${moduleId}/selection`, { node_id: nodeId, approve }).then((r) => r.data),
  quickEditModule: (id: number, moduleId: number, data: { node_id: string; replacement_node_id?: string | null; headline: string; subtitle: string; zoom: number; offset_x: number; offset_y: number; text_x?: number; text_y?: number; font_size?: number; text_color?: string; text_align?: string; text_background?: boolean }) => api.post<CanvasNodeRecord>(`/creative-projects/${id}/plan/modules/${moduleId}/quick-edit`, data).then((r) => r.data),
  compliance: (id: number) => api.get<ComplianceReport>(`/creative-projects/${id}/compliance`).then((r) => r.data),
  productConsistency: (id: number) => api.post<NonNullable<ComplianceReport['product_consistency']>>(`/creative-projects/${id}/product-consistency`, undefined, { timeout: 180000 }).then((r) => r.data),
  qualitySummary:(id:number)=>api.get(`/creative-projects/${id}/quality-summary`).then(r=>r.data),
  visionQualitySummary:(id:number)=>api.post(`/creative-projects/${id}/quality-summary/vision`,undefined,{timeout:300000}).then(r=>r.data),
  retryByQuality:(id:number,nodeId:string)=>api.post(`/creative-projects/${id}/nodes/${nodeId}/retry-by-quality`,undefined,{timeout:600000}).then(r=>r.data),
  approvalIssues:(id:number)=>api.get(`/creative-projects/${id}/approval-issues`).then(r=>r.data),
  rejectModule:(id:number,moduleId:number,data:{issue_type:string;severity:string;action:string;note:string;region?:{x:number;y:number;width:number;height:number}})=>api.post(`/creative-projects/${id}/plan/modules/${moduleId}/reject`,data).then(r=>r.data),
  resolveIssue:(id:number,issueId:number,nodeId:string)=>api.post(`/creative-projects/${id}/approval-issues/${issueId}/resolve`,{node_id:nodeId}).then(r=>r.data),
  regionalRegenerate:(id:number,issueId:number)=>api.post(`/creative-projects/${id}/approval-issues/${issueId}/regional-regenerate`,undefined,{timeout:600000}).then(r=>r.data),
  finalizeHd:(id:number,nodeId:string)=>api.post<CanvasNodeRecord>(`/creative-projects/${id}/nodes/${nodeId}/finalize-hd`).then(r=>r.data),
  qualityRegression:(id:number)=>api.get(`/creative-projects/${id}/quality-regression`).then(r=>r.data),
  exportStoryboard: (id: number, confirmRisks = false) => api.post<{ long_image_url: string; module_count: number; missing_modules: string[]; compliance: ComplianceReport }>(`/creative-projects/${id}/export`, undefined, { params: { confirm_risks: confirmRisks } }).then((r) => r.data),
  createBatch: (id: number, moduleIds: number[] = []) => api.post<StoryboardBatchJob>(`/creative-projects/${id}/batch-generate`, { module_ids: moduleIds }).then((r) => r.data),
  latestBatch: (id: number) => api.get<StoryboardBatchJob | null>(`/creative-projects/${id}/batch-generate/latest`).then((r) => r.data),
  stopBatch: (id: number, jobId: string) => api.put<StoryboardBatchJob>(`/creative-projects/${id}/batch-generate/${jobId}/stop`).then((r) => r.data),
  applyStyle: (id: number, data: { name: string; primary_color: string; accent_color: string; typography: string; whitespace: number; copy_density: number }) => api.post<CreativePlan>(`/creative-projects/${id}/style-versions`, data).then((r) => r.data),
  rollbackStyle: (id: number, versionId: string) => api.post<CreativePlan>(`/creative-projects/${id}/style-versions/${versionId}/rollback`).then((r) => r.data),
  uploadRefined: async (id: number, file: File, parentNodeId?: string) => {
    const form = new FormData(); form.append('file', file); form.append('parent_node_id', parentNodeId || '');
    return api.post<CanvasNodeRecord>(`/creative-projects/${id}/refined-output`, form).then((r) => r.data);
  },
  submitDeliverable: async (id: number, file: File, parentNodeId?: string, note = '') => {
    const form = new FormData();
    form.append('file', file);
    form.append('parent_node_id', parentNodeId || '');
    form.append('note', note);
    return api.post<CanvasNodeRecord>(`/creative-projects/${id}/deliverable`, form).then((r) => r.data);
  },
  markFinal: (id: number, nodeId: string) => api.put<CanvasNodeRecord>(`/creative-projects/${id}/nodes/${nodeId}/final`).then((r) => r.data),
  feedback: (id: number) => api.get<CreativeFeedback[]>(`/creative-projects/${id}/feedback`).then((r) => r.data),
  reviewNode: (id: number, nodeId: string, status: string, reasons: string[] = []) => api.put<CreativeFeedback>(`/creative-projects/${id}/nodes/${nodeId}/feedback`, { status, reasons }).then((r) => r.data),
  learnedProfile: (productId: number) => api.get<LearnedDesignProfile | null>(`/products/${productId}/learned-design-profile`).then((r) => r.data),
};

export const qualityApi={
  rules:(category:string)=>api.get(`/quality/rules/${encodeURIComponent(category)}`).then(r=>r.data),
  saveRules:(category:string,data:{thresholds:Record<string,number>;rules:string[]})=>api.put(`/quality/rules/${encodeURIComponent(category)}`,data).then(r=>r.data),
  ruleVersions:(category:string)=>api.get(`/quality/rules/${encodeURIComponent(category)}/versions`).then(r=>r.data),
  rollbackRules:(category:string,version:number)=>api.post(`/quality/rules/${encodeURIComponent(category)}/versions/${version}/rollback`).then(r=>r.data),
  feedback:(projectId:number,nodeId:string,data:{feedback_type:string;field?:string;note?:string})=>api.post(`/quality/projects/${projectId}/nodes/${nodeId}/feedback`,data).then(r=>r.data),
  samples:(category='')=>api.get('/quality/regression-samples',{params:{category}}).then(r=>r.data),
  createSample:(data:Record<string,unknown>)=>api.post('/quality/regression-samples',data).then(r=>r.data),
  updateSample:(id:number,data:Record<string,unknown>)=>api.put(`/quality/regression-samples/${id}`,data).then(r=>r.data),
  deleteSample:(id:number)=>api.delete(`/quality/regression-samples/${id}`).then(r=>r.data),
  mergedComments:(projectId:number)=>api.get(`/quality/projects/${projectId}/merged-comments`).then(r=>r.data),
  reviewTodos:()=>api.get('/quality/review-todos').then(r=>r.data),
};

export const dashboardApi = {
  stats: () => api.get<DashboardStats>('/dashboard/stats').then((r) => r.data),
};

export const creativeMetricsApi = {
  summary: (params: Record<string, string | number> = {}) => api.get<CreativeMetrics>('/creative-projects/metrics/summary', { params }).then((r) => r.data),
  retry: (projectId: number, generationId: number) => api.post(`/creative-projects/${projectId}/generations/${generationId}/retry`).then((r) => r.data),
};

export const operationsApi = {
  readiness: (productId: number) => api.get(`/operations/products/${productId}/readiness`).then(r=>r.data),
  imageAdmission:(productId:number)=>api.get(`/operations/products/${productId}/image-admission`).then(r=>r.data),
  recommendations: (productId: number, platform='') => api.get('/operations/templates/recommendations',{params:{product_id:productId,platform}}).then(r=>r.data),
  templatePerformance: (templateId: number) => api.get(`/operations/templates/${templateId}/performance`).then(r=>r.data),
  taskDetail: (id:number) => api.get(`/operations/tasks/detail/${id}`).then(r=>r.data),
  retryTasks: (ids:number[]) => api.post('/operations/tasks/retry',{ids}).then(r=>r.data),
  cancelTasks: (ids:number[]) => api.post('/operations/tasks/cancel',{ids}).then(r=>r.data),
  taskStatistics: (params: Record<string, string | number> = {}) => api.get('/operations/tasks/statistics',{params}).then(r=>r.data),
  billingStatus: () => api.get('/operations/billing/status').then(r=>r.data),
  syncBilling: () => api.post('/operations/billing/sync').then(r=>r.data),
  projectReviews: (id:number) => api.get(`/operations/projects/${id}/reviews`).then(r=>r.data),
  reviewAction: (id:number, action:string, note='',extra?:{module_id?:number;assignee_id?:string;due_at?:string|null;blocks_finalize?:boolean}) => api.post(`/operations/projects/${id}/reviews`,{action,note,...extra}).then(r=>r.data),
};

export const productionApi = {
  facts: (productId:number) => api.get(`/production/products/${productId}/facts`).then(r=>r.data),
  saveFact: (productId:number,data:{fact_key:string;label:string;value:string;source_type?:string;source_ref?:string}) => api.put(`/production/products/${productId}/facts`,data).then(r=>r.data),
  confirmFact: (productId:number,factId:number,value?:string) => api.post(`/production/products/${productId}/facts/${factId}/confirm`,undefined,{params:value!==undefined?{value}:undefined}).then(r=>r.data),
  snapshots: (projectId:number) => api.get(`/production/projects/${projectId}/snapshots`).then(r=>r.data),
  compareSnapshots:(projectId:number,left_version:number,right_version:number)=>api.post(`/production/projects/${projectId}/snapshots/compare`,{left_version,right_version}).then(r=>r.data),
  restoreSnapshot:(projectId:number,version:number)=>api.post(`/production/projects/${projectId}/snapshots/${version}/restore`).then(r=>r.data),
  copyProject:(projectId:number)=>api.post(`/production/projects/${projectId}/copy`).then(r=>r.data),
  previewBatch:async(file:File)=>{const form=new FormData();form.append('file',file);return api.post('/production/batches/preview',form).then(r=>r.data)},
  importBatch: async(file:File,meta:{name:string;template_id?:number;platform:string;enqueue:boolean})=>{const form=new FormData();form.append('file',file);form.append('name',meta.name);form.append('platform',meta.platform);form.append('enqueue',String(meta.enqueue));if(meta.template_id)form.append('template_id',String(meta.template_id));return api.post('/production/batches/import',form,{timeout:180000}).then(r=>r.data)},
  batches:()=>api.get('/production/batches').then(r=>r.data),
  batch:(id:string)=>api.get(`/production/batches/${id}`).then(r=>r.data),
  retryBatchItem:(batchId:string,itemId:number)=>api.post(`/production/batches/${batchId}/items/${itemId}/retry`).then(r=>r.data),
  queue:()=>api.get('/production/queue').then(r=>r.data),
  cancelQueue:(id:string)=>api.post(`/production/queue/${id}/cancel`).then(r=>r.data),
  dashboard:()=>api.get('/production/dashboard').then(r=>r.data),
};

export interface CreativeMetrics { total_tasks: number; completed: number; failed: number; success_rate: number; image_count: number; estimated_cost_cny: number; cost_source: 'estimated' | 'provider_bill'; cost_note: string; monthly_budget_cny: number; budget_usage_percent: number; max_concurrency: number; running: number; error_breakdown: Record<string, number>; providers: string[]; recent_tasks: Array<{ id: number; project_id: number; action: string; provider: string; status: string; result_count: number; diagnostic?: { code: string; title: string; suggestion: string; retryable: boolean }; triggered_by: string; trigger_source: string; created_at: string }> }

export default api;
