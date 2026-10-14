import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest';
import {act, cleanup, fireEvent, render, screen, waitFor} from '@testing-library/react';
import {MemoryRouter, Route, Routes} from 'react-router-dom';
import VideoRefinementWorkbench from '../src/pages/VideoRefinementWorkbench';
import {ecommerceVideoTaxonomyApi, videoRefinementApi} from '../src/api/client';
import type {RefinementSource, VideoRefinementTask, VideoRefinementTaskSummary} from '../src/types/videoRefinement';

vi.mock('../src/api/client',()=>({mediaUrl:(value:string)=>value,ecommerceVideoTaxonomyApi:{catalog:vi.fn()},videoRefinementApi:{sources:vi.fn(),list:vi.fn(),get:vi.fn(),state:vi.fn(),create:vi.fn(),analyze:vi.fn(),autoPreview:vi.fn(),updateDraft:vi.fn(),confirm:vi.fn(),cancel:vi.fn(),preview:vi.fn(),accept:vi.fn(),materials:vi.fn(),refreshMaterials:vi.fn(),requirements:vi.fn(),generateVoiceover:vi.fn(),voiceOptions:vi.fn()}}));
const source: RefinementSource = {id:101,product_id:1,product_name:'国光湿巾',filename:'国光湿巾_成片_001.mp4',object_url:'/source.mp4',duration_seconds:22.78,width:720,height:1280,fps:30,has_audio:true,burned_subtitles:true,created_at:'2026-10-06T00:00:00Z'};
const task: VideoRefinementTask = {
  id:1,source_asset_id:101,product_id:1,source_hash:'a'.repeat(64),snapshot_hash:'s'.repeat(64),user_brief:'提升开头吸引力',expected_result:'',focus:['pace'],status:'PLAN_READY',current_revision:1,confirmed_revision:null,preview_supported:true,created_at:'2026-10-06T00:00:00Z',updated_at:'2026-10-06T00:00:00Z',
  source_snapshot:{schema_version:'video_refinement_snapshot.v1',is_mock:false,fixture:'guoguang-001.v1',source:{asset_id:101,product_id:1,product_name:source.product_name,filename:source.filename,object_url:source.object_url,version:'v1',sku:null,content_hash:'a'.repeat(64),duration_ms:22780,width:720,height:1280,aspect_ratio:'9:16',fps:30,has_audio:true,burned_subtitles:true,has_background_music:true},timeline:[{shot_id:'mock-shot-001',start_ms:0,end_ms:5000,label:'示例开场',is_mock:false}],transcript:[],subtitles:[],material_catalog:[],capabilities:{visual:{state:'PLAN_ONLY',reason:'画面仅保存方案'},pace:{state:'PLAN_ONLY',reason:'节奏仅保存方案'},voice:{state:'HUMAN_REQUIRED',reason:'混合音轨不能直接替换口播'},subtitles:{state:'HUMAN_REQUIRED',reason:'硬字幕禁止叠加'},aspect_ratio_locked:true,sku_verified:false}},
  plan_hash:'b'.repeat(64),plan:{schema_version:'video_refinement_plan.v1',source:{} as VideoRefinementTask['source_snapshot']['source'],summary:{objective:'提升开头吸引力',expected_result:'',expected_duration_ms:21980,duration_delta_ms:-800,duration_reason:'缩短开场',confidence_level:'MEDIUM',is_mock:false},items:[{item_id:'item-pace',type:'PACE_RECUT',range:{start_ms:0,end_ms:5000},operation:'压缩开场停顿',reason:'开场吸引力目标',evidence:['mock-shot-001'],confidence_level:'MEDIUM',enabled:true,duration_delta_ms:-800,material_asset_id:null,script:'',subtitle_text:'',risk:'需核对原声连续性',dependencies:[],status:'PROPOSED'}],execution_blockers:[],findings_not_applied:['音乐保持原样']},
};

const taskSummary: VideoRefinementTaskSummary = {
  id:task.id,source_filename:source.filename,user_brief:task.user_brief,status:task.status,
  current_revision:task.current_revision,created_at:task.created_at,updated_at:task.updated_at,
};

beforeEach(()=>{
  vi.clearAllMocks();
  vi.mocked(videoRefinementApi.voiceOptions).mockResolvedValue({version:"test",total:276,default_mode:"auto",items:[]});
  vi.mocked(videoRefinementApi.sources).mockResolvedValue([source]);
  vi.mocked(ecommerceVideoTaxonomyApi.catalog).mockResolvedValue({categories:[],groups:[],public_tags:[],personal_tags:[],presets:[]} as Awaited<ReturnType<typeof ecommerceVideoTaxonomyApi.catalog>>);
  vi.mocked(videoRefinementApi.list).mockResolvedValue([]);
  vi.mocked(videoRefinementApi.get).mockResolvedValue(structuredClone(task));
  vi.mocked(videoRefinementApi.state).mockImplementation((id,signal)=>videoRefinementApi.get(id,signal));
  vi.mocked(videoRefinementApi.autoPreview).mockResolvedValue(structuredClone(task));
  vi.spyOn(HTMLMediaElement.prototype,'pause').mockImplementation(()=>{});
});
afterEach(cleanup);
function mount(path='/video-refinements') {
  return render(<MemoryRouter initialEntries={[path]}><Routes><Route path="/video-refinements" element={<VideoRefinementWorkbench/>}/><Route path="/video-refinements/:id" element={<VideoRefinementWorkbench/>}/></Routes></MemoryRouter>);
}

describe('视频精修工作台',()=>{
  it('仅使用轻量摘要显示任务, 打开任务后才加载详情',async()=>{
    vi.mocked(videoRefinementApi.list).mockResolvedValue([taskSummary]);
    mount();
    fireEvent.click(screen.getByRole('tab',{name:/精修任务/}));
    const link=await screen.findByRole('link',{name:new RegExp(task.user_brief)});
    expect(link.textContent).toContain(source.filename);
    expect(link.textContent).toContain('方案待确认 / r1');
    expect(videoRefinementApi.get).not.toHaveBeenCalled();
    fireEvent.click(link);
    await waitFor(()=>expect(videoRefinementApi.get).toHaveBeenCalledWith(task.id));
    expect(await screen.findByRole('button',{name:'修改方案'})).toBeTruthy();
  });

  it('插入项只编辑插入位置, 保存时保持零长度的源片区间',async()=>{
    const insertion={...task,plan:{...task.plan!,items:[{...task.plan!.items[0],type:'SEGMENT_INSERT' as const,range:{start_ms:0,end_ms:0},duration_delta_ms:1800,material_asset_id:7}]}};
    vi.mocked(videoRefinementApi.get).mockResolvedValue(insertion);
    vi.mocked(videoRefinementApi.updateDraft).mockImplementation(async(_id,input)=>({...insertion,status:'CONFIGURING',current_revision:2,plan:{...insertion.plan,items:input.items}}));
    mount('/video-refinements/1');
    fireEvent.click(await screen.findByRole('button',{name:'修改方案'}));
    expect(screen.queryByLabelText('结束时间, 秒')).toBeNull();
    fireEvent.change(screen.getByLabelText('插入位置, 秒'),{target:{value:'4.77'}});
    fireEvent.click(screen.getByRole('button',{name:'保存方案修改'}));
    await waitFor(()=>expect(videoRefinementApi.updateDraft).toHaveBeenCalledWith(1,{revision:1,items:[expect.objectContaining({range:{start_ms:4770,end_ms:4770},duration_delta_ms:1800})]}));
  });

  it('真实方案确认后可生成预览, 播放核对后才允许保存新版本',async()=>{
    const preview = {id:4,revision:1,plan_hash:task.plan_hash!,status:'ready' as const,output_url:'/real-preview.mp4',error_message:'',validation:{passed:true,duration_seconds:21.98,width:720,height:1280,decode_checked:true}};
    const confirmed = {...task,status:'PLAN_CONFIRMED' as const,confirmed_revision:1,previews:[preview]};
    vi.mocked(videoRefinementApi.get).mockResolvedValue(confirmed);
    vi.mocked(videoRefinementApi.accept).mockResolvedValue({...confirmed,status:'COMPLETED',output_asset_id:201,output_version:2,previews:[{...preview,status:'accepted'}]});
    mount('/video-refinements/1');
    const accept=await screen.findByRole('button',{name:'接受预览并另存精修版本'});
    expect(accept).toHaveProperty('disabled',true);
    expect(screen.queryByText(/媒体与解码检查已通过/)).toBeNull();
    expect(screen.queryByText(/自动媒体检查不代替内容核对/)).toBeNull();
    expect(screen.queryByText(/实际结果需渲染后测量/)).toBeNull();
    expect(screen.getByText(/预览已生成, 请核对预览后另存/)).toBeTruthy();
    expect(screen.getByLabelText('精修预览播放器')).toHaveProperty('src',expect.stringContaining('/real-preview.mp4'));
    fireEvent.click(screen.getByLabelText('我已播放预览并核对音画, 字幕和商品'));
    fireEvent.click(accept);
    const result=await screen.findByRole('link',{name:'查看精修 V2 成片'});
    expect(result.getAttribute('href')).toBe('/ecommerce-final-videos/201');
    expect(videoRefinementApi.accept).toHaveBeenCalledWith(1,4,{revision:1,plan_hash:task.plan_hash});
    expect(screen.queryByRole('button',{name:'取消任务'})).toBeNull();
  });

  it('预览生成失败可以重试, 未生成预览时不能保存新版本',async()=>{
    const failed={...task,status:'PLAN_CONFIRMED' as const,confirmed_revision:1,previews:[{id:4,revision:1,plan_hash:task.plan_hash!,status:'failed' as const,output_url:'',error_message:'实际素材长度不足',validation:{}}]};
    vi.mocked(videoRefinementApi.get).mockResolvedValue(failed);
    vi.mocked(videoRefinementApi.preview).mockResolvedValue({...failed,previews:[{...failed.previews[0],status:'queued'}]});
    mount('/video-refinements/1');
    fireEvent.click(await screen.findByRole('button',{name:'重新生成预览'}));
    await screen.findByText(/等待生成完整预览/);
    expect(videoRefinementApi.preview).toHaveBeenCalledWith(1,{revision:1,plan_hash:task.plan_hash});
    expect(screen.queryByRole('button',{name:'接受预览并另存精修版本'})).toBeNull();
  });

  it('分析失败的素材仍可选择并播放实际文件',async()=>{
    const visual={...task,source_snapshot:{...task.source_snapshot,material_catalog:[{asset_id:7,product_id:1,filename:'分析失败素材.mov',object_url:'/failed-material.mov',content_hash:'f'.repeat(64),duration_ms:0,is_mock_mapping:false,analysis_status:'analysis_failed'}]},plan:{...task.plan!,items:[{...task.plan!.items[0],type:'VISUAL_REPLACE' as const,duration_delta_ms:0,material_asset_id:7}]}};
    vi.mocked(videoRefinementApi.get).mockResolvedValue(visual);
    mount('/video-refinements/1');
    fireEvent.click(await screen.findByRole('button',{name:'修改方案'}));
    expect(screen.getByRole('option',{name:/分析失败, 仍可使用/})).toBeTruthy();
    expect(screen.getByLabelText('候选素材播放器')).toHaveProperty('src',expect.stringContaining('/failed-material.mov'));
  });

  it('旧版示例任务保留记录, 可以从同一成片和目标创建真实任务',async()=>{
    vi.mocked(videoRefinementApi.get).mockResolvedValue({...task,source_snapshot:{...task.source_snapshot,is_mock:true}});
    vi.mocked(videoRefinementApi.create).mockResolvedValue({...task,id:2,status:'CREATED',plan:null});
    mount('/video-refinements/1');
    fireEvent.click(await screen.findByRole('button',{name:'使用真实数据重新开始'}));
    await waitFor(()=>expect(videoRefinementApi.create).toHaveBeenCalledWith({source_asset_id:101,user_brief:task.user_brief,expected_result:'',focus:['pace']}));
  });

  it('任务列表失败时仍可选择成片, 重试只请求任务列表',async()=>{
    vi.mocked(videoRefinementApi.list).mockRejectedValueOnce(new Error('server error')).mockResolvedValueOnce([taskSummary]);
    mount();
    await screen.findByRole('button',{name:`选择 ${source.filename}`});
    expect(screen.queryByText('商品成片库还没有可用视频')).toBeNull();
    fireEvent.click(screen.getByRole('tab',{name:/精修任务/}));
    expect(await screen.findByRole('alert')).toHaveProperty('textContent', expect.stringContaining('精修任务加载失败'));
    expect(screen.queryByText('还没有精修任务')).toBeNull();
    fireEvent.click(screen.getByRole('button',{name:'重新加载'}));
    await screen.findByRole('link',{name:new RegExp(task.user_brief)});
    expect(videoRefinementApi.sources).toHaveBeenCalledTimes(1);
    expect(videoRefinementApi.list).toHaveBeenCalledTimes(2);
  });

  it('成片请求失败时仍可查看任务, 不显示没有成片',async()=>{
    vi.mocked(videoRefinementApi.sources).mockRejectedValueOnce(new Error('server error')).mockResolvedValueOnce([source]);
    vi.mocked(videoRefinementApi.list).mockResolvedValue([taskSummary]);
    mount();
    expect(await screen.findByRole('alert')).toHaveProperty('textContent', expect.stringContaining('商品成片加载失败'));
    expect(screen.queryByText('商品成片库还没有可用视频')).toBeNull();
    expect(screen.queryByText(/共 0 条结果/)).toBeNull();
    fireEvent.click(screen.getByRole('tab',{name:/精修任务/}));
    await screen.findByRole('link',{name:new RegExp(task.user_brief)});
    expect(screen.queryByRole('alert')).toBeNull();
    fireEvent.click(screen.getByRole('tab',{name:'选择商品成片'}));
    fireEvent.click(screen.getByRole('button',{name:'重新加载'}));
    await screen.findByRole('button',{name:`选择 ${source.filename}`});
    expect(videoRefinementApi.sources).toHaveBeenCalledTimes(2);
    expect(videoRefinementApi.list).toHaveBeenCalledTimes(1);
  });

  it('两个请求均失败时分别显示错误, 不显示业务空状态',async()=>{
    vi.mocked(videoRefinementApi.sources).mockRejectedValue(new Error('offline'));
    vi.mocked(videoRefinementApi.list).mockRejectedValue(new Error('offline'));
    mount();
    expect(await screen.findByRole('alert')).toHaveProperty('textContent', expect.stringContaining('商品成片加载失败'));
    expect(screen.queryByText('商品成片库还没有可用视频')).toBeNull();
    fireEvent.click(screen.getByRole('tab',{name:/精修任务/}));
    expect(await screen.findByRole('alert')).toHaveProperty('textContent', expect.stringContaining('精修任务加载失败'));
    expect(screen.queryByText('还没有精修任务')).toBeNull();
  });

  it('任务请求尚未返回时可以立即选择成片',async()=>{
    vi.mocked(videoRefinementApi.list).mockReturnValue(new Promise(()=>{}));
    mount();
    fireEvent.click(await screen.findByRole('button',{name:`选择 ${source.filename}`}));
    expect(screen.getByLabelText('修改意见')).toBeTruthy();
  });

  it('未选择视频时只显示成片选择状态, 搜索可筛选源片',async()=>{
    mount();
    await screen.findByRole('button',{name:`选择 ${source.filename}`});
    expect(screen.queryByLabelText('精修目标')).toBeNull();
    fireEvent.change(screen.getByLabelText('搜索商品成片'),{target:{value:'不存在'}});
    expect(screen.getByText('没有符合筛选条件的商品成片')).toBeTruthy();
    expect(videoRefinementApi.create).not.toHaveBeenCalled();
  });

  it('快捷入口仅预选源片, 填写目标后才创建任务',async()=>{
    vi.mocked(videoRefinementApi.create).mockResolvedValue({...task,status:'CREATED',current_revision:0,plan:null,plan_hash:null});
    mount('/video-refinements?source_asset_id=101');
    fireEvent.change(await screen.findByLabelText('修改意见'),{target:{value:'只修改商品画面'}});
    expect(screen.getByRole('link',{name:'查看商品成片详情'}).getAttribute('href')).toBe('/ecommerce-final-videos/101');
    expect(screen.getByText(/原成片保持不变/).textContent).toContain('商品成片库');
    expect(videoRefinementApi.create).not.toHaveBeenCalled();
    expect(screen.queryByText('重点方向, 可选')).toBeNull();
    expect(screen.queryByLabelText('预期结果, 可选')).toBeNull();
    fireEvent.click(screen.getByRole('button',{name:'开始精修'}));
    await screen.findByRole('button',{name:'确认整套方案'});
    expect(videoRefinementApi.create).toHaveBeenCalledWith({source_asset_id:101,user_brief:'只修改商品画面',duration_preference:'ai_recommend'});
  });

  it('缩短目标等于或大于原片时长时提示错误并禁止提交, 改成 20 秒后可提交',async()=>{
    vi.mocked(videoRefinementApi.create).mockResolvedValue(task);
    mount('/video-refinements?source_asset_id=101');
    fireEvent.change(await screen.findByLabelText('修改意见'),{target:{value:'保留核心卖点和原声, 缩短到 20 秒'}});
    fireEvent.change(screen.getByLabelText('时长目标'),{target:{value:'shorten'}});
    for(const target of ['100','22.78','0','-1']){
      fireEvent.change(screen.getByLabelText('目标时长, 秒'),{target:{value:target}});
      expect(screen.getByRole('button',{name:'开始精修'})).toHaveProperty('disabled',true);
      expect(screen.getByRole('alert')).toBeTruthy();
    }
    expect(videoRefinementApi.create).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText('目标时长, 秒'),{target:{value:'20'}});
    fireEvent.click(screen.getByRole('button',{name:'开始精修'}));
    await waitFor(()=>expect(videoRefinementApi.create).toHaveBeenCalledWith(expect.objectContaining({duration_preference:'shorten',target_duration_ms:20000})));
  });

  it('修改任务目标同样阻止超长缩短目标, 清空目标显式移除旧目标',async()=>{
    vi.mocked(videoRefinementApi.get).mockResolvedValue({...task,source_snapshot:{...task.source_snapshot,schema_version:'video_refinement_snapshot.v2'},options:{duration_preference:'shorten',target_duration_ms:20000}});
    vi.mocked(videoRefinementApi.requirements).mockResolvedValue({...task,current_revision:2});
    mount('/video-refinements/1');
    fireEvent.click(await screen.findByText('修改意见与时长目标'));
    fireEvent.change(screen.getByLabelText('目标时长, 秒'),{target:{value:'100'}});
    expect(screen.getByRole('button',{name:'保存目标并生成预览'})).toHaveProperty('disabled',true);
    expect(videoRefinementApi.requirements).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText('目标时长, 秒'),{target:{value:''}});
    fireEvent.click(screen.getByRole('button',{name:'保存目标并生成预览'}));
    await waitFor(()=>expect(videoRefinementApi.requirements).toHaveBeenCalledWith(1,expect.objectContaining({target_duration_ms:null})));
  });

  it('精修选择页不显示分类标签筛选, 保留搜索和成片操作',async()=>{
    const tag={id:7,name:'核心卖点',scope:'public' as const,status:'active' as const,source:'manual' as const};
    vi.mocked(videoRefinementApi.sources).mockResolvedValue([{...source,taxonomy_tags:[tag],public_tags:[tag]}]);
    mount();
    await screen.findByRole('button',{name:`选择 ${source.filename}`});
    expect(ecommerceVideoTaxonomyApi.catalog).not.toHaveBeenCalled();
    expect(screen.getByLabelText(`预览 ${source.filename}`)).toHaveProperty('src',expect.stringContaining('/source.mp4'));
    expect(screen.queryByRole('heading',{name:'分类与标签'})).toBeNull();
    expect(screen.queryByRole('button',{name:'个人标签'})).toBeNull();
    for(const name of ['筛选商品','筛选时长','筛选画幅','筛选创建时间'])expect(screen.getByRole('combobox',{name})).toBeTruthy();
    fireEvent.change(screen.getByLabelText('搜索商品成片'),{target:{value:'核心卖点'}});
    expect(screen.getByRole('button',{name:`选择 ${source.filename}`})).toBeTruthy();
    expect(screen.getByRole('link',{name:'查看详情'}).getAttribute('href')).toBe('/ecommerce-final-videos/101');
    expect(screen.getByRole('link',{name:'下载'}).getAttribute('href')).toBe('/source.mp4');
    fireEvent.click(screen.getByRole('button',{name:`选择 ${source.filename}`}));
    expect(screen.getByLabelText('修改意见')).toBeTruthy();
  });

  it('商品成片为空时只引导打开商品成片库, 不关联旧成片库',async()=>{
    vi.mocked(videoRefinementApi.sources).mockResolvedValue([]);
    mount();
    expect(await screen.findByText('商品成片库还没有可用视频')).toBeTruthy();
    expect(screen.getByRole('link',{name:'打开商品成片库'}).getAttribute('href')).toBe('/ecommerce-final-videos');
    expect(screen.queryByRole('link',{name:'打开成片库'})).toBeNull();
  });

  it('单项编辑保存当前 revision, 保存后再整体确认',async()=>{
    vi.mocked(videoRefinementApi.updateDraft).mockImplementation(async(_id,input)=>({...task,status:'CONFIGURING',current_revision:2,plan_hash:'c'.repeat(64),plan:{...task.plan!,items:input.items}}));
    vi.mocked(videoRefinementApi.confirm).mockResolvedValue({...task,status:'PLAN_CONFIRMED',current_revision:2,confirmed_revision:2});
    mount('/video-refinements/1');
    fireEvent.click(await screen.findByRole('button',{name:'修改方案'}));
    fireEvent.change(screen.getByLabelText('修改描述'),{target:{value:'提前进入商品展示'}});
    expect(screen.queryByRole('button',{name:'确认整套方案'})).toBeNull();
    fireEvent.click(screen.getByRole('button',{name:'保存方案修改'}));
    fireEvent.click(await screen.findByRole('button',{name:'确认整套方案'}));
    await screen.findByText('r2 已整体确认. 可以生成视频预览.');
    expect(videoRefinementApi.updateDraft).toHaveBeenCalledWith(1,{revision:1,items:[expect.objectContaining({operation:'提前进入商品展示'})]});
    expect(videoRefinementApi.confirm).toHaveBeenCalledWith(1,{revision:2,plan_hash:'c'.repeat(64),acknowledge_risks:false});
  });

  it('保存冲突时保留本地修改, 不确认服务器的另一版方案',async()=>{
    vi.mocked(videoRefinementApi.updateDraft).mockRejectedValue({response:{data:{detail:'方案已更新或任务已取消, 请刷新后重试'}}});
    mount('/video-refinements/1');
    fireEvent.click(await screen.findByRole('button',{name:'修改方案'}));
    fireEvent.change(screen.getByLabelText('修改描述'),{target:{value:'保留本地修改'}});
    fireEvent.click(screen.getByRole('button',{name:'保存方案修改'}));
    await screen.findByRole('alert');
    expect(screen.getByLabelText('修改描述')).toHaveProperty('value','保留本地修改');
    expect(videoRefinementApi.confirm).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button',{name:'放弃修改'}));
    expect(screen.queryByLabelText('修改描述')).toBeNull();
  });

  it('低置信度方案需要勾选风险, 修改后旧确认失效',async()=>{
    const low={...task,plan:{...task.plan!,items:[{...task.plan!.items[0],confidence_level:'LOW' as const}]}};
    vi.mocked(videoRefinementApi.get).mockResolvedValue(low);
    vi.mocked(videoRefinementApi.confirm).mockResolvedValue({...low,status:'PLAN_CONFIRMED',confirmed_revision:1});
    vi.mocked(videoRefinementApi.updateDraft).mockImplementation(async(_id,input)=>({...low,status:'CONFIGURING',current_revision:2,confirmed_revision:null,plan:{...low.plan,items:input.items}}));
    mount('/video-refinements/1');
    const confirm=await screen.findByRole('button',{name:'确认整套方案'});
    expect(confirm).toHaveProperty('disabled',true);
    fireEvent.click(screen.getByLabelText('我已核对低置信度建议和素材, 配音, 字幕的处理限制'));
    fireEvent.click(confirm);
    await screen.findByText('r1 已整体确认. 可以生成视频预览.');
    fireEvent.click(screen.getByRole('button',{name:'修改方案'}));
    fireEvent.change(screen.getByLabelText('缩短时长, 秒'),{target:{value:'0.4'}});
    fireEvent.click(screen.getByRole('button',{name:'保存方案修改'}));
    await waitFor(()=>expect(screen.getByRole('button',{name:'确认整套方案'})).toHaveProperty('disabled',true));
    expect(screen.queryByText('r1 已整体确认. 可以生成视频预览.')).toBeNull();
  });
});

function v2Task(): VideoRefinementTask {
  const value=structuredClone(task);
  value.source_snapshot.schema_version='video_refinement_snapshot.v2';
  value.plan!.schema_version='video_refinement_plan.v2';
  value.source_snapshot.source.burned_subtitles=false;
  value.plan!.items=[{...value.plan!.items[0],type:'VISUAL_REPLACE',duration_delta_ms:0,range:{start_ms:1000,end_ms:2000},operation:'替换商品画面',material_segments:[],narration_strategy:'keep_source'}];
  value.plan!.summary.expected_duration_ms=22780;value.plan!.summary.duration_delta_ms=0;
  value.options={duration_preference:'preserve'};
  return value;
}

describe('视频精修 v2',()=>{
  it('片段关键信息默认显示, 重复选声和技术依据折叠且不隐藏具体风险',async()=>{
    const value=v2Task();
    const selection={speaker:'shared',name:'贴心女声 2.0',speech_rate:0,mode:'auto' as const,reason:'适配商品讲解',basis:'content_only' as const};
    value.plan!.analysis={model:'test',mode:'full_video',timeline:[],source_hash:value.source_hash,voice_selection:selection};
    value.plan!.items[0]={...value.plan!.items[0],type:'SEGMENT_INSERT',range:{start_ms:1000,end_ms:1000},duration_delta_ms:1500,narration_strategy:'tts',script:'新增商品讲解',voiceover_id:7,evidence:['material-205'],material_segments:[{asset_id:205,source_range:{start_ms:500,end_ms:2000}}],confidence_level:'LOW',risk:'选段含讲话口型, 请核对与台词是否一致; 选段含讲话口型, 请核对与台词是否一致; 局部配音使用适配或手动选择的中文音色, 请试听核对与原声及音乐的衔接'};
    value.source_snapshot.material_catalog=[{asset_id:205,product_id:1,filename:'湿巾使用演示.mp4',object_url:'/material.mp4',content_hash:'m',duration_ms:4000,is_mock_mapping:false}];
    value.voiceovers=[{id:7,item_id:value.plan!.items[0].item_id,revision:1,script:'新增商品讲解',fingerprint:'f',status:'ready',duration_ms:1500,object_url:'/voice.wav',error_message:'',parameters:{speaker:'shared',selection},timing:{}}];
    vi.mocked(videoRefinementApi.get).mockResolvedValue(value);
    mount('/video-refinements/1');
    const inspector=await screen.findByRole('region',{name:'修改项详情'});
    expect(screen.getByRole('heading',{name:'修改 1 · 片段插入详情'})).toBeTruthy();
    expect(screen.getByText('本片段: 新增配音 · 素材原声关闭')).toBeTruthy();
    expect(screen.getByText('原片 00:01.000 处插入 / 新增 1.50 秒')).toBeTruthy();
    expect(screen.getByText('湿巾使用演示.mp4')).toBeTruthy();
    expect(screen.getByText('取用 00:00.500 - 00:02.000 · 1.50 秒')).toBeTruthy();
    expect(screen.queryByRole('region',{name:'新增配音策略'})).toBeNull();
    expect(screen.queryByText('配音说明')).toBeNull();
    expect(screen.queryByText('贴心女声 2.0')).toBeNull();
    expect(screen.queryByText('适配商品讲解')).toBeNull();
    expect(screen.getAllByText('选段含讲话口型, 请核对与台词是否一致')).toHaveLength(1);
    expect(screen.queryByText(/局部配音使用适配或手动选择/)).toBeNull();
    const details=screen.getByText('修改依据与处理说明').closest('details');
    expect(details).toHaveProperty('open',false);
    expect(inspector.querySelector('.refine-evidence')).toBeNull();
    fireEvent.click(screen.getByText('修改依据与处理说明'));
    expect(details).toHaveProperty('open',true);
    expect(screen.getByText('分析依据: material-205')).toBeTruthy();
    expect(videoRefinementApi.confirm).not.toHaveBeenCalled();
    expect(screen.getByRole('button',{name:'确认整套方案'})).toHaveProperty('disabled',true);
  });

  it('已有预览使用实测时长, 本地草稿改动后恢复预计时长',async()=>{
    const value=v2Task();
    value.previews=[{id:4,revision:1,plan_hash:value.plan_hash!,status:'ready',output_url:'/preview.mp4',error_message:'',validation:{duration_seconds:24.1}}];
    vi.mocked(videoRefinementApi.get).mockResolvedValue(value);
    mount('/video-refinements/1');
    await screen.findByRole('button',{name:'修改方案'});
    expect(screen.getByText('24.10 秒')).toBeTruthy();
    expect(screen.queryByText('预计')).toBeNull();
    fireEvent.click(screen.getByRole('button',{name:'修改方案'}));
    fireEvent.change(screen.getByLabelText('修改描述'),{target:{value:'修改后的商品画面'}});
    expect(screen.queryByText('24.10 秒')).toBeNull();
    expect(screen.getByText('预计')).toBeTruthy();
    expect(screen.queryByRole('button',{name:'接受预览并另存精修版本'})).toBeNull();
    expect(videoRefinementApi.accept).not.toHaveBeenCalled();
  });

  it('v3 展示局部选段证据和采样限制, 不将核验结果宣称为 SKU 已确认',async()=>{
    const value=v2Task();value.plan!.schema_version='video_refinement_plan.v3';
    value.plan!.items[0].material_segments=[{asset_id:205,source_range:{start_ms:5500,end_ms:6500},candidate_id:'local-1'}];
    value.plan!.local_selection=[{item_id:value.plan!.items[0].item_id,asset_id:205,candidate_id:'local-1',source_range:{start_ms:5500,end_ms:6500},safe_range:{start_ms:3000,end_ms:9000},preferred_range:{start_ms:5000,end_ms:7000},reason:'包装在中部稳定清晰',verification:{method:'sampled_frames',sampled_times_ms:[3000,5000,8999],risks:['请核对中间画面']},product_match:{sku_verified:false,evidence:'包装颜色与当前商品一致'}}];
    vi.mocked(videoRefinementApi.get).mockResolvedValue(value);
    mount('/video-refinements/1');
    expect(await screen.findByText('素材 205 选择依据: 包装在中部稳定清晰')).toBeTruthy();
    expect(screen.getByText(/已采样 3 个时间点, 不代表连续观看/)).toBeTruthy();
    expect(screen.getByText(/SKU 尚需人工核对/)).toBeTruthy();
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('局部定位阶段显示实际阶段而不是预估百分比',async()=>{
    const value=v2Task();value.analysis_state={id:1,revision:1,status:'analyzing',progress:{stage:'localizing'},error_message:''};
    vi.mocked(videoRefinementApi.get).mockResolvedValue(value);
    mount('/video-refinements/1');
    expect(await screen.findByText('正在定位商品展示片段')).toBeTruthy();
  });
  it('自动帧修正显示为非阻塞结果并展示实际源区间',async()=>{
    const value=v2Task();value.plan!.items[0].material_segments=[{asset_id:205,source_range:{start_ms:0,end_ms:4000}}];
    value.plan!.automatic_adjustments=[{item_id:'item-pace',asset_id:205,code:'FRAME_BUDGET_CORRECTED',before_range:{start_ms:0,end_ms:4033},after_range:{start_ms:0,end_ms:4000},removed_frames:1,message:'已自动缩短最后一个商品展示片段一帧, 配音完整保留.'}];
    vi.mocked(videoRefinementApi.get).mockResolvedValue(value);
    mount('/video-refinements/1');
    expect(await screen.findByText(/已自动缩短最后一个商品展示片段一帧/)).toBeTruthy();
    expect(screen.getByText('素材 205')).toBeTruthy();
    expect(screen.getByText('取用 00:00.000 - 00:04.000 · 4.00 秒')).toBeTruthy();
    expect(screen.queryByRole('alert')).toBeNull();
    expect(screen.queryByText('当前方案缺少的执行条件')).toBeNull();
    expect(screen.queryByLabelText('对此要求的回答')).toBeNull();
  });

  it('音频执行阻塞不提示用户缺少素材或需要澄清',async()=>{
    const value=v2Task();value.plan!.execution_blockers=[{item_id:'item-pace',code:'VOICEOVER_DURATION',category:'audio',message:'配音长于授权区间, 请精简台词'}];
    value.plan!.requirements_resolution=[{requirement_id:'req',text:'增加展示镜头',status:'EXECUTABLE',reason:'已规划',question:'',item_ids:['item-pace'],decision:'pending',answer:'',execution_blockers:value.plan!.execution_blockers}];
    vi.mocked(videoRefinementApi.get).mockResolvedValue(value);
    mount('/video-refinements/1');
    expect(await screen.findByText('配音长于授权区间, 请精简台词')).toBeTruthy();
    expect(screen.queryByText('等待素材')).toBeNull();
    expect(screen.queryByLabelText('对此要求的回答')).toBeNull();
    expect(screen.getByRole('button',{name:'确认整套方案'})).toHaveProperty('disabled',true);
  });

  it('旧方案缺少执行条件时可以直接启动自动预览, 无需手动选择素材',async()=>{
    const value=v2Task();value.plan!.execution_blockers=[{item_id:'item-pace',code:'VOICEOVER_REQUIRED',message:'缺少配音'}];
    value.plan!.requirements_resolution=[{requirement_id:'req',text:'补充商品画面',status:'AWAITING_MATERIAL',reason:'关联修改尚未具备执行条件',item_ids:['item-pace'],decision:'pending',answer:''}];
    vi.mocked(videoRefinementApi.get).mockResolvedValue(value);
    vi.mocked(videoRefinementApi.autoPreview).mockResolvedValue({...value,analysis_state:{id:1,revision:1,status:'queued',progress:{},error_message:''}});
    mount('/video-refinements/1');
    expect(await screen.findByRole('button',{name:'确认整套方案'})).toHaveProperty('disabled',true);
    expect(screen.queryByLabelText('对此要求的回答')).toBeNull();
    expect(screen.queryByRole('button',{name:'生成或重试局部配音'})).toBeNull();
    fireEvent.click(screen.getByRole('button',{name:'AI 精修并生成预览'}));
    await waitFor(()=>expect(videoRefinementApi.autoPreview).toHaveBeenCalledWith(1,1));
    expect(videoRefinementApi.confirm).not.toHaveBeenCalled();
  });

  it('阻塞要求不能通过通用风险勾选确认, 放弃要求后重新规划',async()=>{
    const value=v2Task();value.plan!.requirements_resolution=[{requirement_id:'req-1',text:'保留全部台词且缩到10秒',status:'CONFLICT',reason:'配音长于目标',question:'请选择优先要求',item_ids:[],decision:'pending',answer:''}];
    value.plan!.execution_blockers=[{item_id:'',code:'REQUIREMENT_UNRESOLVED',message:'目标时长冲突'}];
    vi.mocked(videoRefinementApi.get).mockResolvedValue(value);
    const changed={...value,current_revision:2,plan:null,plan_hash:null};
    vi.mocked(videoRefinementApi.requirements).mockResolvedValue(changed);
    vi.mocked(videoRefinementApi.autoPreview).mockResolvedValue({...changed,analysis_state:{id:1,revision:2,status:'queued',progress:{},error_message:''}});
    mount('/video-refinements/1');
    expect(await screen.findByRole('button',{name:'确认整套方案'})).toHaveProperty('disabled',true);
    fireEvent.click(screen.getByRole('button',{name:'放弃这条要求并重新规划'}));
    await waitFor(()=>expect(videoRefinementApi.requirements).toHaveBeenCalledWith(1,{revision:1,decisions:[{requirement_id:'req-1',decision:'waived',answer:''}]}));
    expect(videoRefinementApi.autoPreview).toHaveBeenCalledWith(1,2);
    expect(videoRefinementApi.confirm).not.toHaveBeenCalled();
  });

  it('全量素材支持后续页面选择局部区间, 保存源区间而非完整素材',async()=>{
    const value=v2Task();vi.mocked(videoRefinementApi.get).mockResolvedValue(value);
    const material={asset_id:99,product_id:1,filename:'最后一页素材.mp4',object_url:'/material.mp4',content_hash:'c'.repeat(64),duration_ms:18000,is_mock_mapping:false};
    vi.mocked(videoRefinementApi.materials).mockResolvedValueOnce({items:[],total:501,next_cursor:'cursor-2',snapshot_hash:'s'}).mockResolvedValue({items:[material],total:501,next_cursor:null,snapshot_hash:'s'});
    vi.mocked(videoRefinementApi.updateDraft).mockImplementation(async(_id,input)=>({...value,current_revision:2,plan:{...value.plan!,items:input.items}}));
    mount('/video-refinements/1');fireEvent.click(await screen.findByRole('button',{name:'修改方案'}));
    const next=await screen.findByRole('button',{name:'下一页'});await waitFor(()=>expect(next).toHaveProperty('disabled',false));fireEvent.click(next);
    fireEvent.click(await screen.findByRole('button',{name:'最后一页素材.mp4 18.00 秒'}));
    fireEvent.change(screen.getByLabelText('素材源起点, 秒'),{target:{value:'6'}});fireEvent.change(screen.getByLabelText('素材源终点, 秒'),{target:{value:'7'}});
    fireEvent.click(screen.getByRole('button',{name:'使用此选段'}));fireEvent.click(screen.getByRole('button',{name:'保存方案修改'}));
    await waitFor(()=>expect(videoRefinementApi.updateDraft).toHaveBeenCalledWith(1,{revision:1,items:[expect.objectContaining({material_segments:[{asset_id:99,source_range:{start_ms:6000,end_ms:7000}}]})]}));
    expect(videoRefinementApi.materials).toHaveBeenCalledWith(1,'','cursor-2');
    expect(screen.queryByText('采用素材原声, 保持素材音画绑定')).toBeNull();
  });

  it('生成配音不会自动绑定, 试听后显式绑定再保存',async()=>{
    const value=v2Task();value.plan!.items[0]={...value.plan!.items[0],type:'SEGMENT_INSERT',range:{start_ms:1000,end_ms:1000},script:'新增台词',narration_strategy:'tts',voiceover_id:null};
    vi.mocked(videoRefinementApi.get).mockResolvedValue(value);
    const voice={id:7,item_id:'item-pace',revision:1,script:'新增台词',fingerprint:'f',status:'ready',duration_ms:1000,object_url:'/voice.wav',error_message:'',parameters:{speaker:'default'},timing:{segments:[{start:0,end:1,text:'新增台词'}]}};
    vi.mocked(videoRefinementApi.generateVoiceover).mockResolvedValue({...value,voiceovers:[voice]});
    vi.mocked(videoRefinementApi.updateDraft).mockImplementation(async(_id,input)=>({...value,current_revision:2,plan:{...value.plan!,items:input.items}}));
    mount('/video-refinements/1');fireEvent.click(await screen.findByRole('button',{name:'修改方案'}));fireEvent.click(screen.getByRole('button',{name:'生成或重试局部配音'}));
    fireEvent.click(await screen.findByRole('button',{name:'绑定到当前草稿'}));
    expect(videoRefinementApi.updateDraft).not.toHaveBeenCalled();
    expect(screen.getByLabelText('局部配音试听').getAttribute('src')).toBe('/voice.wav');
    fireEvent.click(screen.getByRole('button',{name:'保存方案修改'}));
    await waitFor(()=>expect(videoRefinementApi.updateDraft).toHaveBeenCalledWith(1,{revision:1,items:[expect.objectContaining({voiceover_id:7})]}));
  });
});

describe('自由修改意见入口',()=>{
  it('自由意见创建成功后自动发起一次分析',async()=>{
    const created={...v2Task(),status:'CREATED' as const,current_revision:0,plan:null,plan_hash:null};
    vi.mocked(videoRefinementApi.create).mockResolvedValue(created);
    vi.mocked(videoRefinementApi.get).mockResolvedValue(created);
    vi.mocked(videoRefinementApi.autoPreview).mockResolvedValue({...created,analysis_state:{id:1,revision:0,status:'queued',progress:{},error_message:''}});
    mount('/video-refinements?source_asset_id=101');
    fireEvent.change(await screen.findByLabelText('修改意见'),{target:{value:'演示有些拖沓, 想保留原来的声音, 请帮我看看怎么改更自然'}});
    fireEvent.click(screen.getByRole('button',{name:'开始精修'}));
    await waitFor(()=>expect(videoRefinementApi.autoPreview).toHaveBeenCalledWith(1,0));
    expect(videoRefinementApi.autoPreview).toHaveBeenCalledTimes(1);
    expect(screen.queryByText('方案字段无效, 请检查目标文本, 修改描述和时间范围后重试')).toBeNull();
  });
});

describe('后台状态恢复',()=>{
  it.each(['disabled','stopped'] as const)('精修 worker 为 $0 时明确提示, 不显示正在调用 AI',async(status)=>{
    const message=status==='disabled'?'后台精修服务已关闭, 请联系管理员启用. 已排队任务保留, 无需重复提交.':'后台精修服务未运行, 请联系管理员检查服务. 已排队任务保留, 无需重复提交.';
    const value={...v2Task(),status:'CREATED' as const,plan:null,plan_hash:null,worker_state:{status,message},analysis_state:{id:1,revision:1,status:'queued',progress:{},error_message:'',queue_wait_seconds:480}};
    vi.mocked(videoRefinementApi.get).mockResolvedValue(value);
    mount('/video-refinements/1');
    expect(await screen.findByText(message)).toBeTruthy();
    expect(screen.queryByText('正在准备精修方案')).toBeNull();
    expect(screen.getByRole('button',{name:'AI 精修并生成预览'})).toHaveProperty('disabled',true);
    expect(videoRefinementApi.autoPreview).not.toHaveBeenCalled();
  });

  it('较长排队显示实际等待时间, 不误报 AI 正在运行',async()=>{
    const value={...v2Task(),status:'CREATED' as const,plan:null,plan_hash:null,worker_state:{status:'running' as const,message:''},analysis_state:{id:1,revision:1,status:'queued',progress:{},error_message:'',queue_wait_seconds:480}};
    vi.mocked(videoRefinementApi.get).mockResolvedValue(value);
    mount('/video-refinements/1');
    expect(await screen.findByText('已排队约 8 分钟, 尚未开始分析. 无需重复提交.')).toBeTruthy();
    expect(screen.queryByText('正在准备精修方案')).toBeNull();
  });

  it('同修订轮询恢复 worker 状态, 无需重复提交排队任务',async()=>{
    const value={...v2Task(),status:'CREATED' as const,plan:null,plan_hash:null,worker_state:{status:'disabled' as const,message:'后台精修服务已关闭'},analysis_state:{id:1,revision:1,status:'queued',progress:{},error_message:''}};
    const running={...value,worker_state:{status:'running' as const,message:''},analysis_state:{...value.analysis_state,status:'analyzing',progress:{stage:'matching'}}};
    vi.mocked(videoRefinementApi.get).mockResolvedValueOnce(value).mockResolvedValue(running);
    vi.useFakeTimers();
    try{
      await act(async()=>{mount('/video-refinements/1')});
      expect(screen.getByText('后台精修服务已关闭')).toBeTruthy();
      await act(()=>vi.advanceTimersByTimeAsync(2000));
      expect(screen.queryByText('后台精修服务已关闭')).toBeNull();
      expect(screen.getByText('正在匹配修改意见')).toBeTruthy();
      expect(videoRefinementApi.autoPreview).not.toHaveBeenCalled();
    }finally{cleanup();vi.useRealTimers();}
  });

  it('没有排队作业时也能发现 worker 已恢复并解锁生成',async()=>{
    const value={...v2Task(),status:'CREATED' as const,plan:null,plan_hash:null,worker_state:{status:'stopped' as const,message:'后台精修服务未运行'}};
    vi.mocked(videoRefinementApi.get).mockResolvedValueOnce(value).mockResolvedValue({...value,worker_state:{status:'running',message:''}});
    vi.useFakeTimers();
    try{
      await act(async()=>{mount('/video-refinements/1')});
      expect(screen.getByRole('button',{name:'AI 精修并生成预览'})).toHaveProperty('disabled',true);
      await act(()=>vi.advanceTimersByTimeAsync(2000));
      expect(screen.getByRole('button',{name:'AI 精修并生成预览'})).toHaveProperty('disabled',false);
      expect(screen.queryByText('后台精修服务未运行')).toBeNull();
    }finally{cleanup();vi.useRealTimers();}
  });

  it('自动预览完成后更新提示, 不再显示正在生成',async()=>{
    const value=v2Task();value.plan!.automatic_preview=true;
    const created={...value,status:'CREATED' as const,plan:null,plan_hash:null};
    const preview={id:4,revision:2,plan_hash:value.plan_hash!,status:'rendering' as const,output_url:'',error_message:'',validation:{}};
    const rendering={...value,status:'PLAN_CONFIRMED' as const,current_revision:2,confirmed_revision:2,previews:[preview],analysis_state:{id:1,revision:1,status:'ready',progress:{},error_message:''}};
    const ready={...rendering,previews:[{...preview,status:'ready' as const,output_url:'/preview.mp4'}]};
    vi.mocked(videoRefinementApi.get).mockResolvedValueOnce(created).mockResolvedValue(ready);
    vi.mocked(videoRefinementApi.autoPreview).mockResolvedValue(rendering);
    vi.useFakeTimers();
    try{
      await act(async()=>{mount('/video-refinements/1')});
      await act(async()=>{fireEvent.click(screen.getByRole('button',{name:'AI 精修并生成预览'}))});
      expect(screen.getByText('精修方案已就绪, 视频预览正在生成.')).toBeTruthy();
      await act(()=>vi.advanceTimersByTimeAsync(2000));
      expect(screen.getByText('精修预览已生成, 请播放核对后另存精修版本.')).toBeTruthy();
      expect(screen.queryByText('精修方案已就绪, 视频预览正在生成.')).toBeNull();
    }finally{cleanup();vi.useRealTimers();}
  });

  it.each([
    {status:'queued',hasPlan:false,message:'分析已排队, 将匹配已分析素材并生成方案.'},
    {status:'analyzing',hasPlan:false,message:'正在分析并生成精修方案, 请稍候.'},
    {status:'failed',hasPlan:false,message:''},
    {status:'ready',hasPlan:false,message:''},
    {status:'ready',hasPlan:true,message:'真实 AI 精修方案已生成.'},
    {status:'needs_user_input',hasPlan:true,message:'精修方案已生成, 请处理需要澄清或补充的要求.'},
    {status:'unknown',hasPlan:true,message:''},
    {status:'superseded',hasPlan:true,message:''},
  ])('分析返回 $status, 有方案 $hasPlan 时提示符合实际状态',async({status,hasPlan,message})=>{
    const value=v2Task();
    const created={...value,status:'CREATED' as const,plan:null,plan_hash:null};
    vi.mocked(videoRefinementApi.get).mockResolvedValue(created);
    vi.mocked(videoRefinementApi.autoPreview).mockResolvedValue({...hasPlan?value:created,analysis_state:{id:1,revision:1,status,progress:{},error_message:status==='failed'?'AI 方案格式无效':''}});
    mount('/video-refinements/1');
    fireEvent.click(await screen.findByRole('button',{name:'AI 精修并生成预览'}));
    await waitFor(()=>expect(screen.getByRole('button',{name:hasPlan?'修改方案':'AI 精修并生成预览'})).toHaveProperty('disabled',status==='queued'||status==='analyzing'));
    if(message)expect(await screen.findByText(message)).toBeTruthy();
    expect(screen.queryByText('真实 AI 精修方案已生成.')).toBe(message==='真实 AI 精修方案已生成.'?screen.getByText(message):null);
    if(status==='failed')expect(screen.getByRole('alert').textContent).toContain('AI 方案格式无效');
  });

  it('轮询从排队转为分析失败时移除过期提示并保留重试入口',async()=>{
    const created={...v2Task(),status:'CREATED' as const,plan:null,plan_hash:null};
    const queued={...created,analysis_state:{id:1,revision:1,status:'queued',progress:{},error_message:''}};
    const analyzing={...queued,analysis_state:{...queued.analysis_state,status:'analyzing'}};
    const failed={...queued,analysis_state:{...queued.analysis_state,status:'failed',error_message:'AI 方案格式无效, 已保留素材匹配结果'}};
    vi.mocked(videoRefinementApi.get).mockResolvedValueOnce(created).mockResolvedValueOnce(analyzing).mockResolvedValue(failed);
    vi.mocked(videoRefinementApi.autoPreview).mockResolvedValue(queued);
    vi.useFakeTimers();
    try{
      await act(async()=>{mount('/video-refinements/1')});
      await act(async()=>{fireEvent.click(screen.getByRole('button',{name:'AI 精修并生成预览'}))});
      expect(screen.getByText('分析已排队, 将匹配已分析素材并生成方案.')).toBeTruthy();
      await act(()=>vi.advanceTimersByTimeAsync(2000));
      expect(screen.getByText('正在分析并生成精修方案, 请稍候.')).toBeTruthy();
      expect(screen.queryByText('分析已排队, 将匹配已分析素材并生成方案.')).toBeNull();
      await act(()=>vi.advanceTimersByTimeAsync(2000));
      expect(screen.getByRole('alert').textContent).toContain('已保留素材匹配结果');
      expect(screen.getByRole('button',{name:'重试后台分析'})).toHaveProperty('disabled',false);
      expect(screen.queryByText('正在分析并生成精修方案, 请稍候.')).toBeNull();
      expect(screen.queryByText('真实 AI 精修方案已生成.')).toBeNull();
    }finally{cleanup();vi.useRealTimers();}
  });

  it.each(['ready','needs_user_input'])('轮询到 $0 时加载方案并更新排队提示',async(status)=>{
    const value=v2Task();
    const created={...value,status:'CREATED' as const,plan:null,plan_hash:null};
    const queued={...created,analysis_state:{id:1,revision:1,status:'queued',progress:{},error_message:''}};
    const ready={...value,current_revision:2,analysis_state:{...queued.analysis_state,status}};
    vi.mocked(videoRefinementApi.get).mockResolvedValueOnce(created).mockResolvedValue(ready);
    vi.mocked(videoRefinementApi.autoPreview).mockResolvedValue(queued);
    vi.useFakeTimers();
    try{
      await act(async()=>{mount('/video-refinements/1')});
      await act(async()=>{fireEvent.click(screen.getByRole('button',{name:'AI 精修并生成预览'}))});
      await act(()=>vi.advanceTimersByTimeAsync(2000));
      expect(screen.getByRole('button',{name:'修改方案'})).toBeTruthy();
      expect(screen.getByText(status==='ready'?'真实 AI 精修方案已生成.':'精修方案已生成, 请处理需要澄清或补充的要求.')).toBeTruthy();
      expect(screen.queryByText('分析已排队, 将匹配已分析素材并生成方案.')).toBeNull();
    }finally{cleanup();vi.useRealTimers();}
  });

  it('短暂连接失败会自动恢复, 不残留预览错误提示',async()=>{
    const analyzing={...task,source_snapshot:{...task.source_snapshot,schema_version:'video_refinement_snapshot.v2' as const},status:'CREATED' as const,plan:null,plan_hash:null,analysis_state:{id:1,revision:1,status:'analyzing',progress:{available:38,stage:'matching'},error_message:''}};
    vi.mocked(videoRefinementApi.get).mockResolvedValueOnce(analyzing).mockRejectedValueOnce(new Error('temporary')).mockRejectedValueOnce(new Error('temporary')).mockRejectedValueOnce(new Error('temporary')).mockResolvedValue(analyzing);
    vi.useFakeTimers();
    try{
      await act(async()=>{mount('/video-refinements/1')});
      expect(screen.getByText('正在匹配修改意见')).toBeTruthy();
      await act(()=>vi.advanceTimersByTimeAsync(14000));
      expect(screen.getByText('任务状态更新暂时中断, 正在自动重试')).toBeTruthy();
      expect(screen.queryByRole('alert')).toBeNull();
      await act(()=>vi.advanceTimersByTimeAsync(16000));
      expect(screen.queryByText('任务状态更新暂时中断, 正在自动重试')).toBeNull();
    }finally{cleanup();vi.useRealTimers();}
  });

  it('状态请求未结束时不叠加轮询, 离开页面会取消请求',async()=>{
    const analyzing={...v2Task(),status:'CREATED' as const,plan:null,plan_hash:null,analysis_state:{id:1,revision:1,status:'analyzing',progress:{stage:'matching'},error_message:''}};
    let resolvePoll!: (value:VideoRefinementTask)=>void;
    const pending=new Promise<VideoRefinementTask>(resolve=>{resolvePoll=resolve});
    vi.mocked(videoRefinementApi.get).mockResolvedValueOnce(analyzing).mockReturnValueOnce(pending).mockResolvedValue(analyzing);
    vi.useFakeTimers();
    try{
      await act(async()=>{mount('/video-refinements/1')});
      await act(()=>vi.advanceTimersByTimeAsync(20000));
      expect(videoRefinementApi.get).toHaveBeenCalledTimes(2);
      const signal=vi.mocked(videoRefinementApi.get).mock.calls[1][1];
      expect(signal?.aborted).toBe(false);
      await act(async()=>resolvePoll(analyzing));
      await act(()=>vi.advanceTimersByTimeAsync(2000));
      expect(videoRefinementApi.get).toHaveBeenCalledTimes(3);
      cleanup();
      expect(signal?.aborted).toBe(true);
      await act(()=>vi.advanceTimersByTimeAsync(30000));
      expect(videoRefinementApi.get).toHaveBeenCalledTimes(3);
    }finally{cleanup();vi.useRealTimers();}
  });

  it('同一版本仅轮询轻量状态, 不反复加载素材快照和方案',async()=>{
    const analyzing={...v2Task(),status:'CREATED' as const,plan:null,plan_hash:null,analysis_state:{id:1,revision:1,status:'analyzing',progress:{stage:'matching'},error_message:''}};
    vi.mocked(videoRefinementApi.get).mockResolvedValue(analyzing);
    vi.mocked(videoRefinementApi.state).mockResolvedValue({id:analyzing.id,status:analyzing.status,current_revision:analyzing.current_revision,analysis_state:analyzing.analysis_state});
    vi.useFakeTimers();
    try{
      await act(async()=>{mount('/video-refinements/1')});
      await act(()=>vi.advanceTimersByTimeAsync(10000));
      expect(videoRefinementApi.get).toHaveBeenCalledTimes(1);
      expect(videoRefinementApi.state).toHaveBeenCalledTimes(5);
      expect(screen.getByText('正在匹配修改意见')).toBeTruthy();
    }finally{cleanup();vi.useRealTimers();}
  });
});
