import {api} from './client';

export type LibraryProduct = {id:number; image_url:string; introduction:string; created_at:string; updated_at:string};
export type LibrarySegment = {highlight_id:string;start:number;end:number;description:string;product_visibility?:'clear'|'partial'|'none'|'uncertain';audio_mode?:'mute_ok'|'keep_original'|'needs_review';on_screen_text?:string[];corrected_by_user?:boolean};
export type LibraryAsset = {id:number; product_id:number; kind:'image'|'video'; filename:string; object_url:string; size_bytes:number; created_at:string;analysis_status?:string;analysis_summary?:string;analysis_error?:string;segments?:LibrarySegment[]};
export type LibraryFinishedVideo = {id:number; filename:string; object_url:string; duration_seconds:number; created_at:string; status:string};

export const materialLibraryApi = {
  products: () => api.get<LibraryProduct[]>('/material-library/products').then(response => response.data),
  product: (id:number) => api.get<LibraryProduct>(`/material-library/products/${id}`).then(response => response.data),
  create: (image:File, introduction:string) => {
    const form = new FormData(); form.append('image', image); form.append('introduction', introduction);
    return api.post<LibraryProduct>('/material-library/products', form, {timeout:900000}).then(response => response.data);
  },
  update: (id:number, introduction:string, image?:File) => {
    const form = new FormData(); form.append('introduction', introduction); if(image) form.append('image', image);
    return api.patch<LibraryProduct>(`/material-library/products/${id}`, form, {timeout:900000}).then(response => response.data);
  },
  assets: (id:number) => api.get<LibraryAsset[]>(`/material-library/products/${id}/assets`).then(response => response.data),
  finishedVideos: (id:number) => api.get<LibraryFinishedVideo[]>(`/material-library/products/${id}/finished-videos`).then(response => response.data),
  upload: (id:number, file:File, onProgress?:(value:number)=>void) => {
    const form = new FormData(); form.append('file', file);
    return api.post<LibraryAsset>(`/material-library/products/${id}/assets`, form, {
      timeout:900000, onUploadProgress:event => {if(event.total) onProgress?.(Math.round(event.loaded / event.total * 100));},
    }).then(response => response.data);
  },
  remove: (productId:number, assetId:number) => api.delete(`/material-library/products/${productId}/assets/${assetId}`),
  reanalyze: (productId:number, assetId:number) => api.post<LibraryAsset>(`/material-library/products/${productId}/assets/${assetId}/reanalyze`).then(response=>response.data),
  correctSegment: (productId:number, assetId:number, highlightId:string, values:{start_ms:number;end_ms:number;description:string}) =>
    api.patch<LibraryAsset>(`/material-library/products/${productId}/assets/${assetId}/segments/${encodeURIComponent(highlightId)}`,values).then(response=>response.data),
};
