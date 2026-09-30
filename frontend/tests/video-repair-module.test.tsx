import {cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {MemoryRouter} from 'react-router-dom';
import {afterEach,beforeEach,describe,expect,it,vi} from 'vitest';
import VideoRepairWorkbench from '../src/components/VideoRepairWorkbench';
import VideoRepairWorkbenchV4 from '../src/components/VideoRepairWorkbenchV4';
import {qianchuanApi,videoRepairApi} from '../src/api/client';
import type {VideoRepairTask} from '../src/api/client';

vi.mock('../src/api/client',async()=>{
  const actual=await vi.importActual<typeof import('../src/api/client')>('../src/api/client');
  return {...actual,videoRepairApi:{...actual.videoRepairApi,get:vi.fn(),selectCandidate:vi.fn(),updateDraft:vi.fn(),approve:vi.fn(),render:vi.fn(),materials:vi.fn(),updateV4Draft:vi.fn(),uploadAttachment:vi.fn(),createTts:vi.fn(),createPreview:vi.fn(),acceptPreview:vi.fn(),retryPreview:vi.fn(),retryAnalysis:vi.fn(),cancel:vi.fn()}};
});

const task:VideoRepairTask={
  id:4,status:'NEEDS_CONFIRMATION',input_id:9,review_feedback_id:null,source_asset_id:22,base_plan_version_id:7,
  input:{id:9,source_kind:'MANUAL',audit_platform:'INTERNAL',raw_reason:'视频 6–10 秒画面可能引发误解',suggestions:['替换对应画面'],time_ranges:[{start:6,end:10}],occurred_at:'2026-09-28T00:00:00',created_at:'2026-09-28T00:00:00'},
  diagnosis:{normalized_category:'MISLEADING_VISUAL_CLAIM',content_fixable:true,affected_modalities:['visual'],recommended_action:'VISUAL_REPLACE',explanation:'建议只替换问题画面，保留原口播和字幕。',issue_ranges:[{start:6,end:10,precision:'EXACT',source:'INPUT',confidence:1,evidence:'输入事实包含明确时间段'}]},
  repair_plan:{schema_version:'video_repair_plan.v1',primary_action:'VISUAL_REPLACE',warnings:[],actions:[{action_id:'repair-1',type:'VISUAL_REPLACE',target_range:{start:6,end:10},affected_track:'video_track',candidate_ids:['asset-2:h1'],selected_candidate_id:'asset-2:h1',candidates:[{id:'asset-2:h1',source_asset_id:2,source_asset_public_id:'asset-2',highlight_id:'h1',source_range:{start:2,end:8},duration_seconds:6,content_role:'product_demo',description:'合规商品展示',object_url:'/candidate.mp4',has_audio:true,score:.9}]}]},
  selected_candidates:{'repair-1':'asset-2:h1'},output_asset_id:null,output_plan_version_id:null,
  source_asset:{id:22,asset_id:'asset-v1',filename:'v1.mp4',object_url:'/v1.mp4',duration_seconds:12,status:'analyzed'},output_asset:null,error_code:'',error_message:'',approved_at:null,started_at:null,completed_at:null,created_at:'2026-09-28T00:00:00',updated_at:'2026-09-28T00:00:01',
};

describe('审核失败修改独立模块',()=>{
  beforeEach(()=>vi.clearAllMocks());
  afterEach(()=>cleanup());
  it('区分输入事实和 AI 推断，并可确认方案',async()=>{
    vi.mocked(videoRepairApi.approve).mockResolvedValue({...task,status:'APPROVED'});
    render(<MemoryRouter><VideoRepairWorkbench initialTask={task}/></MemoryRouter>);
    const rejectedVideo=screen.getByLabelText('审批未通过的原视频');
    expect(rejectedVideo.getAttribute('src')).toContain('/v1.mp4');
    expect(screen.getByText('待修复原片')).toBeTruthy();
    expect(screen.getByText('审核失败事实')).toBeTruthy();expect(screen.getByText('AI 判断与证据')).toBeTruthy();
    expect(screen.getByText(/6.0–10.0 秒 · EXACT/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button',{name:'确认并冻结方案'}));
    await waitFor(()=>expect(videoRepairApi.approve).toHaveBeenCalledWith(4,task.updated_at));
  });
  it('修改时间段后调用服务端草案更新并重新匹配候选',async()=>{
    vi.mocked(videoRepairApi.updateDraft).mockResolvedValue(task);
    render(<MemoryRouter><VideoRepairWorkbench initialTask={task}/></MemoryRouter>);
    fireEvent.change(screen.getByLabelText('开始时间'),{target:{value:'5.5'}});
    fireEvent.change(screen.getByLabelText('结束时间'),{target:{value:'9.5'}});
    fireEvent.click(screen.getByRole('button',{name:'保存并重新匹配候选'}));
    await waitFor(()=>expect(videoRepairApi.updateDraft).toHaveBeenCalledWith(4,{
      updated_at:task.updated_at,action_type:'VISUAL_REPLACE',
      target_range:{start_ms:5500,end_ms:9500},selected_candidate_id:'asset-2:h1',
    }));
  });
  it('完成后只提供视频域动作',()=>{
    const completed={...task,status:'COMPLETED',output_asset_id:23,output_plan_version_id:8,output_asset:{id:23,asset_id:'asset-v2',filename:'v2.mp4',object_url:'/v2.mp4',duration_seconds:12,status:'analyzed'}};
    render(<MemoryRouter><VideoRepairWorkbench initialTask={completed}/></MemoryRouter>);
    expect(screen.getByText('原始 V1（保持不变）')).toBeTruthy();expect(screen.getByText('修改后 V2（新成片）')).toBeTruthy();
    expect(screen.getByRole('link',{name:'查看 V2'}).getAttribute('href')).toBe('/final-videos/23');
    expect(screen.queryByRole('button',{name:/投流/})).toBeNull();
  });
  it('修复 API 已从千川客户端移出',()=>{
    expect('createRepairTask' in qianchuanApi).toBe(false);expect(typeof videoRepairApi.create).toBe('function');
  });
  it('V4 工作台先人工决策，再通过可视化素材卡保存并生成预览',async()=>{
    const v4:VideoRepairTask={...task,status:'CONFIGURING',schema_version:'video_repair_task.v2',draft_revision:2,
      diagnosis:{...task.diagnosis,recommended_action:'VISUAL_REPLACE',confidence_level:'HIGH',evidence_sufficiency:'SUFFICIENT',suggested_ranges:[{start_ms:6000,end_ms:10000}]},
      repair_plan:{} as VideoRepairTask['repair_plan'],previews:[]};
    vi.mocked(videoRepairApi.materials).mockResolvedValue([{id:31,asset_id:'material-31',filename:'material.mp4',object_url:'/material.mp4',content_hash:'hash',duration_seconds:8,width:1080,height:1920,has_audio:true,asset_kind:'material',summary:'商品使用场景',recommended_range:{start:1,end:5},score:.82,recommendation_level:'RECOMMENDED',reasons:['时长匹配'],warnings:[]}]);
    vi.mocked(videoRepairApi.updateV4Draft).mockResolvedValue({...v4,draft_revision:3,status:'CONFIGURING'});
    vi.mocked(videoRepairApi.createPreview).mockResolvedValue({id:8,draft_revision:3,plan_hash:'plan',status:'QUEUED',object_url:'',content_hash:'',duration_seconds:0,width:0,height:0,error_code:'',error_message:'',created_at:'2026-09-29T00:00:00',completed_at:null});
    vi.mocked(videoRepairApi.get).mockResolvedValue({...v4,draft_revision:3,status:'PREVIEW_QUEUED'});
    render(<MemoryRouter><VideoRepairWorkbenchV4 initialTask={v4}/></MemoryRouter>);
    await waitFor(()=>expect(videoRepairApi.materials).toHaveBeenCalledWith(4));
    expect(screen.queryByLabelText('V4 开始时间')).toBeNull();
    fireEvent.change(screen.getByLabelText('人工审核决定'),{target:{value:'MODIFY'}});
    expect(screen.getByText(/material.mp4/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button',{name:'选择此素材'}));
    fireEvent.change(screen.getByLabelText('V4 开始时间'),{target:{value:'5'}});
    fireEvent.change(screen.getByLabelText('V4 结束时间'),{target:{value:'8'}});
    fireEvent.click(screen.getByRole('button',{name:/保存修改并生成预览/}));
    await waitFor(()=>expect(videoRepairApi.updateV4Draft).toHaveBeenCalledWith(4,2,expect.objectContaining({
      type:'VISUAL_REPLACE',target_range:{start_ms:5000,end_ms:8000},
      material:expect.objectContaining({ref_type:'VIDEO_ASSET',ref_id:31}),
    }),''));
    await waitFor(()=>expect(videoRepairApi.createPreview).toHaveBeenCalledWith(4));
    expect(screen.queryByRole('button',{name:/接受此预览/})).toBeNull();
  });
  it('内容插入优先使用 AI 从素材库找到的画面，只要求补充音频',async()=>{
    const v4:VideoRepairTask={...task,status:'CONFIGURING',schema_version:'video_repair_task.v2',draft_revision:2,
      diagnosis:{...task.diagnosis,recommended_action:'CONTENT_INSERT',confidence_level:'LOW',evidence_sufficiency:'INSUFFICIENT',
        suggested_ranges:[{start_ms:6000,end_ms:10000}],ai_proposal:{summary:'已从同商品素材库找到实际使用过程画面；仍需补充确认后的口播音频。',requires_input:['audio'],action_template:{type:'CONTENT_INSERT',insert_at_ms:6000,duration_ms:4000,visual:{ref_type:'VIDEO_ASSET',ref_id:31,source_range:{start_ms:1000,end_ms:5000},content_hash:'hash'}}}},
      repair_plan:{} as VideoRepairTask['repair_plan'],previews:[],capabilities:['VISUAL_REPLACE','AUDIO_REPLACE','CONTENT_INSERT'],tts_available:false};
    vi.mocked(videoRepairApi.materials).mockResolvedValue([{id:31,asset_id:'material-31',filename:'过程演示.mp4',object_url:'/material.mp4',content_hash:'hash',duration_seconds:8,width:1080,height:1920,has_audio:true,asset_kind:'material',summary:'产品实际使用过程'}]);
    render(<MemoryRouter><VideoRepairWorkbenchV4 initialTask={v4}/></MemoryRouter>);
    await waitFor(()=>expect(videoRepairApi.materials).toHaveBeenCalledWith(4));
    fireEvent.change(screen.getByLabelText('人工审核决定'),{target:{value:'ACCEPT_AI'}});
    expect(screen.getByText('AI 已从素材库找到画面')).toBeTruthy();
    expect(screen.getByText(/默认沿用素材原声/)).toBeTruthy();
    expect(screen.getByRole('button',{name:/沿用素材原声并生成预览/}).hasAttribute('disabled')).toBe(false);
  });
  it('内容插入没有审核时间点时，保留 AI 找到的素材并转入人工确认位置',async()=>{
    const v4:VideoRepairTask={...task,status:'AWAITING_MANUAL_DECISION',schema_version:'video_repair_task.v2',draft_revision:2,
      diagnosis:{...task.diagnosis,recommended_action:'CONTENT_INSERT',confidence_level:'LOW',evidence_sufficiency:'INSUFFICIENT',suggested_ranges:[],
        ai_proposal:{summary:'已从同商品素材库找到实际使用过程画面；仍需人工确认插入位置并补充口播音频。',requires_input:['insert_position','audio'],action_template:{type:'CONTENT_INSERT',duration_ms:3000,visual:{ref_type:'VIDEO_ASSET',ref_id:31,source_range:{start_ms:1000,end_ms:4000},content_hash:'hash'}}}},
      repair_plan:{} as VideoRepairTask['repair_plan'],previews:[],capabilities:['VISUAL_REPLACE','AUDIO_REPLACE','CONTENT_INSERT'],tts_available:false};
    vi.mocked(videoRepairApi.materials).mockResolvedValue([{id:31,asset_id:'material-31',filename:'过程演示.mp4',object_url:'/material.mp4',content_hash:'hash',duration_seconds:8,width:1080,height:1920,has_audio:true,asset_kind:'material',summary:'产品实际使用过程'}]);
    render(<MemoryRouter><VideoRepairWorkbenchV4 initialTask={v4}/></MemoryRouter>);
    await waitFor(()=>expect(videoRepairApi.materials).toHaveBeenCalledWith(4));
    fireEvent.change(screen.getByLabelText('人工审核决定'),{target:{value:'ACCEPT_AI'}});
    expect(screen.getByText(/审核意见没有时间点/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button',{name:'确认插入位置'}));
    expect(screen.getByLabelText('插入位置')).toBeTruthy();
    expect(screen.getByText('已选择：过程演示.mp4')).toBeTruthy();
  });
  it('AI 给出低置信度插入点并沿用素材原声时可直接生成预览',async()=>{
    const action={type:'CONTENT_INSERT',insert_at_ms:33400,duration_ms:3000,
      visual:{ref_type:'VIDEO_ASSET',ref_id:31,source_range:{start_ms:0,end_ms:3000},content_hash:'hash'},
      audio:{source_type:'VIDEO_ASSET',ref_type:'VIDEO_ASSET',ref_id:31,source_range:{start_ms:0,end_ms:3000},content_hash:'hash'}};
    const v4:VideoRepairTask={...task,status:'AWAITING_MANUAL_DECISION',schema_version:'video_repair_task.v2',draft_revision:2,
      diagnosis:{...task.diagnosis,recommended_action:'CONTENT_INSERT',confidence_level:'LOW',evidence_sufficiency:'INSUFFICIENT',suggested_ranges:[],
        ai_proposal:{summary:'建议在 33.4 秒插入；候选素材原声将与画面一起插入。',requires_input:[],action_template:action,action,
          placement:{recommended_ms:33400,alternatives:[25900,41000],confidence:.35,confidence_level:'LOW',basis:'原片暂无字幕和镜头证据',evidence:['DURATION_HEURISTIC']}}},
      repair_plan:{} as VideoRepairTask['repair_plan'],previews:[],capabilities:['VISUAL_REPLACE','AUDIO_REPLACE','CONTENT_INSERT'],tts_available:false};
    vi.mocked(videoRepairApi.materials).mockResolvedValue([{id:31,asset_id:'material-31',filename:'过程演示.mp4',object_url:'/material.mp4',content_hash:'hash',duration_seconds:8,width:1080,height:1920,has_audio:true,asset_kind:'material'}]);
    vi.mocked(videoRepairApi.updateV4Draft).mockResolvedValue({...v4,draft_revision:3,status:'CONFIGURING'});
    vi.mocked(videoRepairApi.createPreview).mockResolvedValue({id:9,draft_revision:3,plan_hash:'plan',status:'QUEUED',object_url:'',content_hash:'',duration_seconds:0,width:0,height:0,error_code:'',error_message:'',created_at:'2026-09-29T00:00:00',completed_at:null});
    vi.mocked(videoRepairApi.get).mockResolvedValue({...v4,draft_revision:3,status:'PREVIEW_QUEUED'});
    render(<MemoryRouter><VideoRepairWorkbenchV4 initialTask={v4}/></MemoryRouter>);
    await waitFor(()=>expect(videoRepairApi.materials).toHaveBeenCalledWith(4));
    expect(screen.getByText(/33.4 秒 · LOW 35%/)).toBeTruthy();
    expect(screen.getByText(/备选：25.9 秒 \/ 41.0 秒/)).toBeTruthy();
    fireEvent.change(screen.getByLabelText('人工审核决定'),{target:{value:'ACCEPT_AI'}});
    fireEvent.click(screen.getByRole('button',{name:'接受 AI 方案并生成预览'}));
    await waitFor(()=>expect(videoRepairApi.updateV4Draft).toHaveBeenCalledWith(4,2,action,''));
    await waitFor(()=>expect(videoRepairApi.createPreview).toHaveBeenCalledWith(4));
  });
});
