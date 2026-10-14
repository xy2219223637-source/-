import {readFileSync} from 'node:fs';
import {resolve} from 'node:path';
import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest';
import {cleanup, fireEvent, render, screen, waitFor} from '@testing-library/react';
import {RefinementVoicePicker, VoiceMatchSummary} from '../src/pages/RefinementVoicePicker';
import {GoalEditor, VoiceoverEditor} from '../src/pages/VideoRefinementControls';
import {videoRefinementApi} from '../src/api/client';
import type {RefinementSpeakerCatalog, VideoRefinementPlanItem, VideoRefinementTask} from '../src/types/videoRefinement';

vi.mock('../src/api/client',()=>({mediaUrl:(value:string)=>value,videoRefinementApi:{voiceOptions:vi.fn(),requirements:vi.fn(),autoPreview:vi.fn(),generateVoiceover:vi.fn()}}));
const FEMALE='zh_female_shuangkuaisisi_uranus_bigtts';
const MALE='zh_male_m191_uranus_bigtts';
const catalog: RefinementSpeakerCatalog={version:'test',total:276,default_mode:'auto',items:readFileSync(resolve('../backend/app/services/data/refinement_chinese_voices.tsv'),'utf8').trim().split('\n').map(line=>{
  const [id,name]=line.trim().split('\t');
  return {id,name,gender:id.includes('_female_')?'female':'male',languages:['zh'],resource_id:'seed-tts-2.0'};
})};
const item={item_id:'voice',script:'完整新增台词',narration_strategy:'tts',voiceover_id:7,tts_speaker:null,subtitle_strategy:'regenerate'} as VideoRefinementPlanItem;
const task={id:1,current_revision:2,plan_hash:'h'.repeat(64),user_brief:'新增一句商品讲解',options:{duration_preference:'ai_recommend',tts_speaker:null},source_snapshot:{source:{duration_ms:10000}},plan:{analysis:{voice_selection:{speaker:MALE,name:'云舟 2.0',speech_rate:0,mode:'auto',reason:'参考原片低声区和商品讲解风格',basis:'source_audio_and_content'}}},voiceovers:[]} as unknown as VideoRefinementTask;

beforeEach(()=>{
  vi.clearAllMocks();
  vi.mocked(videoRefinementApi.voiceOptions).mockResolvedValue(catalog);
});
afterEach(cleanup);

describe('中文配音音色选择',()=>{
  it('真实目录显示276种, 默认自动适配, 可按男女声筛选',async()=>{
    render(<RefinementVoicePicker value={null} onChange={vi.fn()}/>);
    await screen.findByText(/共276种中文相关音色, 当前筛选276种/);
    expect(screen.getByLabelText('配音音色')).toHaveProperty('value','');
    expect(screen.getAllByRole('option')).toHaveLength(280);
    fireEvent.change(screen.getByLabelText('音色类型'),{target:{value:'female'}});
    expect(screen.getByText(/当前筛选110种/)).toBeTruthy();
    fireEvent.change(screen.getByLabelText('音色类型'),{target:{value:'male'}});
    expect(screen.getByText(/当前筛选166种/)).toBeTruthy();
  });

  it('名称和ID搜索均可定位音色, 无结果时仍能返回自动适配',async()=>{
    const onChange=vi.fn();
    render(<RefinementVoicePicker value={null} onChange={onChange}/>);
    await screen.findByText(/共276种/);
    fireEvent.change(screen.getByLabelText('搜索中文音色'),{target:{value:'云舟'}});
    expect(screen.getByText(/当前筛选1种/)).toBeTruthy();
    fireEvent.change(screen.getByLabelText('配音音色'),{target:{value:MALE}});
    expect(onChange).toHaveBeenLastCalledWith(MALE);
    fireEvent.change(screen.getByLabelText('搜索中文音色'),{target:{value:FEMALE}});
    expect(screen.getByRole('option',{name:'爽快思思 2.0'})).toBeTruthy();
    fireEvent.change(screen.getByLabelText('搜索中文音色'),{target:{value:'不存在'}});
    expect(screen.getByText(/请调整搜索或音色类型/)).toBeTruthy();
    fireEvent.change(screen.getByLabelText('配音音色'),{target:{value:''}});
    expect(onChange).toHaveBeenLastCalledWith(null);
  });

  it('筛选不隐藏当前已选音色, 不会意外切回自动',async()=>{
    render(<RefinementVoicePicker value={FEMALE} onChange={vi.fn()}/>);
    await screen.findByText(/共276种/);
    fireEvent.change(screen.getByLabelText('音色类型'),{target:{value:'male'}});
    expect(screen.getByLabelText('配音音色')).toHaveProperty('value',FEMALE);
    expect(screen.getByRole('option',{name:'爽快思思 2.0'})).toBeTruthy();
  });

  it('音色加载失败可重试, 不以空目录替代真实结果',async()=>{
    vi.mocked(videoRefinementApi.voiceOptions).mockRejectedValueOnce(new Error('offline')).mockResolvedValueOnce(catalog);
    render(<RefinementVoicePicker value={null} onChange={vi.fn()}/>);
    await screen.findByRole('alert');
    expect(screen.getByLabelText('配音音色')).toHaveProperty('disabled',true);
    fireEvent.click(screen.getByRole('button',{name:'重新加载音色'}));
    await screen.findByText(/共276种/);
    expect(screen.getByLabelText('配音音色')).toHaveProperty('disabled',false);
  });

  it('改变局部音色清空旧绑定, 未保存时不能生成配音',async()=>{
    const onChange=vi.fn();
    render(<VoiceoverEditor task={task} item={item} disabled={false} dirty action={vi.fn()} onChange={onChange}/>);
    await screen.findByText(/共276种/);
    fireEvent.change(screen.getByLabelText('配音音色'),{target:{value:FEMALE}});
    expect(onChange).toHaveBeenCalledWith({tts_speaker:FEMALE,voiceover_id:null});
    expect(screen.getByRole('button',{name:'生成或重试局部配音'})).toHaveProperty('disabled',true);
    expect(screen.getByText(/自动推荐: 云舟/)).toBeTruthy();
  });

  it('其他音色的排队资产不阻止当前音色生成或被错误绑定',async()=>{
    const selected={...item,tts_speaker:FEMALE};
    const value={...task,voiceovers:[{id:5,script:item.script,status:'queued',parameters:{speaker:MALE}}]} as VideoRefinementTask;
    render(<VoiceoverEditor task={value} item={selected} disabled={false} dirty={false} action={vi.fn()} onChange={vi.fn()}/>);
    await screen.findByText(/共276种/);
    expect(screen.getByRole('button',{name:'生成或重试局部配音'})).toHaveProperty('disabled',false);
    expect(screen.queryByText(/配音排队中/)).toBeNull();
  });

  it('任务音色设置和精修目标一起保存, 然后按新版本生成',async()=>{
    const action=vi.fn(async(_name,work)=>work());
    const changed={...task,current_revision:3};
    vi.mocked(videoRefinementApi.requirements).mockResolvedValue(changed);
    vi.mocked(videoRefinementApi.autoPreview).mockResolvedValue(changed);
    render(<GoalEditor task={task} disabled={false} action={action}/>);
    fireEvent.click(screen.getByText('修改意见与时长目标'));
    await screen.findByText(/共276种/);
    fireEvent.change(screen.getByLabelText('新增配音音色'),{target:{value:MALE}});
    fireEvent.click(screen.getByRole('button',{name:'保存目标并生成预览'}));
    await waitFor(()=>expect(videoRefinementApi.requirements).toHaveBeenCalledWith(1,expect.objectContaining({revision:2,tts_speaker:MALE})));
    await waitFor(()=>expect(videoRefinementApi.autoPreview).toHaveBeenCalledWith(1,3));
  });

  it('缺少可靠原声时明确显示按内容选声, 不宣称声音复刻',()=>{
    render(<VoiceMatchSummary selection={{speaker:MALE,name:'云舟 2.0',speech_rate:0,mode:'auto',reason:'仅参考商品画面',basis:'content_only'}}/>);
    expect(screen.getByText(/按视频内容选声/)).toBeTruthy();
    expect(screen.getByText(/不做声音复刻/)).toBeTruthy();
  });
});
