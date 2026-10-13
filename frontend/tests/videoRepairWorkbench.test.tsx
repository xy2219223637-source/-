import {render,screen} from '@testing-library/react';
import {MemoryRouter} from 'react-router-dom';
import {describe,expect,it,vi} from 'vitest';
import VideoRepairWorkbenchV4 from '../src/components/VideoRepairWorkbenchV4';
import type {VideoRepairTask} from '../src/api/client';

vi.mock('../src/api/client',()=>({
  mediaUrl:(url:string)=>url,
  videoRepairApi:{materials:async()=>[{id:7,asset_kind:'material',filename:'material.mp4',
    object_url:'/material.mp4',duration_seconds:59,has_audio:true}]},
}));

const priorTask={
  id:36,status:'CONFIGURING',draft_revision:1,updated_at:'2026-10-13T00:00:00',
  input:{raw_reason:'需要替换一段画面'},
  source_asset:{id:1,filename:'original.mp4',object_url:'/original.mp4',duration_seconds:59.77,status:'analyzed'},
  diagnosis:{recommended_action:'VISUAL_REPLACE',issues:[{issue_id:'issue_1',description:'画面不符合要求',affected_modalities:['visual'],suggested_action:'VISUAL_REPLACE'}],
    primary_issue_id:'issue_1',ai_proposal:{action:null,summary:'请选择替换素材',requires_input:['visual_material'],
      action_template:{type:'VISUAL_REPLACE',target_range:{start_ms:6000,end_ms:10000},fit_mode:'COVER'}}},
  repair_plan:{},previews:[],error_code:'',error_message:'',tts_available:false,
} as unknown as VideoRepairTask;

describe('VideoRepairWorkbenchV4',()=>{
  it('renders an existing configuring task and shows the two audit actions',()=>{
    render(<MemoryRouter><VideoRepairWorkbenchV4 initialTask={priorTask}/></MemoryRouter>);
    expect(screen.getAllByText('请选择替换素材').length).toBeGreaterThan(0);
    expect(screen.getByRole('button',{name:'要求 AI 重新分析'})).toBeTruthy();
    expect(screen.getByRole('button',{name:'接受 AI 方案并生成预览'})).toBeTruthy();
  });

  it('shows only the selected material in the insertion preview with separate timeline labels',async()=>{
    const task={...priorTask,diagnosis:{...priorTask.diagnosis,recommended_action:'CONTENT_INSERT',
      ai_proposal:{action:{type:'CONTENT_INSERT',insert_at_ms:7600,duration_ms:3000,
        visual:{ref_id:7,source_range:{start_ms:11000,end_ms:14000}},audio:{source_type:'VIDEO_ASSET'}},
        summary:'插入产品展示',requires_input:[],placement:{recommended_ms:7600,
          alternatives:[5800,9300],confidence:0.35,confidence_level:'LOW',basis:'镜头边界'}}}} as unknown as VideoRepairTask;
    render(<MemoryRouter><VideoRepairWorkbenchV4 initialTask={task}/></MemoryRouter>);
    await screen.findByText('素材自身截取起点：11.0 秒');
    const insertSection=screen.getByText('待插入素材预览').closest('section');
    expect(insertSection?.querySelectorAll('video')).toHaveLength(1);
    expect(insertSection?.querySelector('video')?.getAttribute('src')).toBe('/material.mp4');
    expect(insertSection?.textContent).toContain('原片上的备选插入位置：5.8 秒 / 9.3 秒');
  });
});
