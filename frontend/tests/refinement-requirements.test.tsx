import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest';
import {cleanup, fireEvent, render, screen, waitFor} from '@testing-library/react';
import {RequirementPanel} from '../src/pages/VideoRefinementControls';
import {videoRefinementApi} from '../src/api/client';
import type {RefinementRequirement, VideoRefinementTask} from '../src/types/videoRefinement';

vi.mock('../src/api/client',()=>({mediaUrl:(value:string)=>value,videoRefinementApi:{requirements:vi.fn(),autoPreview:vi.fn()}}));
type Requirement = RefinementRequirement;
const requirement=(patch: Partial<Requirement>={}): Requirement=>({requirement_id:'shorten',text:'缩短到20秒',reason:'目标需要澄清',question:'请明确优先保留哪个目标?',item_ids:[],status:'NEEDS_CLARIFICATION',decision:'pending',answer:'',...patch});
const taskFor=(rows: Requirement[])=>({id:1,current_revision:2,plan:{requirements_resolution:rows}} as VideoRefinementTask);
const action=vi.fn(async(_name:string,work:()=>Promise<VideoRefinementTask>)=>{await work()});
const conflicts=()=>[
  requirement({status:'CONFLICT',conflict_ids:['audio']}),
  requirement({requirement_id:'audio',text:'完整保留原声',status:'CONFLICT',conflict_ids:['shorten']}),
];

beforeEach(()=>{
  vi.clearAllMocks();
  vi.mocked(videoRefinementApi.requirements).mockResolvedValue({...taskFor([]),current_revision:3});
  vi.mocked(videoRefinementApi.autoPreview).mockResolvedValue(taskFor([]));
});
afterEach(cleanup);

describe('目标驱动精修的必要澄清',()=>{
  it('无具体问题的技术阻塞和不支持项不生成回答表单',()=>{
    const {container}=render(<RequirementPanel task={taskFor([
      requirement({question:' ',execution_blockers:[{item_id:'i',code:'analysis_failed',message:'分析失败',category:'technical'}]}),
      requirement({requirement_id:'unsupported',status:'UNSUPPORTED',question:'请补充信息'}),
      requirement({requirement_id:'material',status:'AWAITING_MATERIAL',question:''}),
    ])} disabled={false} action={action}/>);
    expect(container.innerHTML).toBe('');
    expect(screen.queryByRole('textbox')).toBeNull();
  });

  it('旧方案派生的编辑授权问题不覆盖已可执行的原始要求',()=>{
    const {container}=render(<RequirementPanel task={taskFor([
      requirement({planning_status:'EXECUTABLE',reason:'关联编辑项缺少独立的用户授权',question:'请明确该编辑项是否允许修改台词或增加视频时长',execution_blockers:[{item_id:'edit',code:'EDIT_AUTHORIZATION_REQUIRED',message:'缺少独立授权',category:'input'}]}),
      requirement({requirement_id:'satisfied',planning_status:'ALREADY_SATISFIED',question:'请授权关联编辑项'}),
    ])} disabled={false} action={action}/>);
    expect(container.innerHTML).toBe('');
  });

  it('相互关联的同一冲突只询问一次, 批量保存回答后只重新规划一次',async()=>{
    render(<RequirementPanel task={taskFor(conflicts())} disabled={false} action={action}/>);
    expect(screen.getAllByRole('textbox')).toHaveLength(1);
    expect(screen.getAllByText('请明确优先保留哪个目标?')).toHaveLength(1);
    expect(screen.getByText('缩短到20秒',{selector:'li'})).toBeTruthy();
    expect(screen.getByText('完整保留原声',{selector:'li'})).toBeTruthy();
    fireEvent.change(screen.getByLabelText('对此冲突的回答'),{target:{value:'优先缩短, 允许裁剪原声'}});
    fireEvent.click(screen.getByRole('button',{name:'保存回答并重新规划'}));
    await waitFor(()=>expect(videoRefinementApi.autoPreview).toHaveBeenCalledWith(1,3));
    expect(videoRefinementApi.requirements).toHaveBeenCalledExactlyOnceWith(1,{revision:2,decisions:[
      {requirement_id:'shorten',decision:'answered',answer:'优先缩短, 允许裁剪原声'},
      {requirement_id:'audio',decision:'answered',answer:'优先缩短, 允许裁剪原声'},
    ]});
    expect(videoRefinementApi.autoPreview).toHaveBeenCalledTimes(1);
  });

  it('放弃操作明确命名且仅放弃所选要求, 不放弃整个冲突组',async()=>{
    render(<RequirementPanel task={taskFor(conflicts())} disabled={false} action={action}/>);
    fireEvent.click(screen.getByRole('button',{name:'放弃要求: 完整保留原声'}));
    await waitFor(()=>expect(videoRefinementApi.autoPreview).toHaveBeenCalledTimes(1));
    expect(videoRefinementApi.requirements).toHaveBeenCalledExactlyOnceWith(1,{revision:2,decisions:[{requirement_id:'audio',decision:'waived',answer:''}]});
  });

  it('同样问题但无关联或原因不同的冲突不合并',()=>{
    const [first,second]=conflicts();
    render(<RequirementPanel task={taskFor([first,{...second,reason:'另一个冲突'},requirement({requirement_id:'other',status:'CONFLICT'})])} disabled={false} action={action}/>);
    expect(screen.getAllByRole('textbox')).toHaveLength(3);
  });

  it('独立澄清和真实素材问题保留独立回答, 不显示已放弃项',async()=>{
    render(<RequirementPanel task={taskFor([requirement(),requirement({requirement_id:'material',status:'AWAITING_MATERIAL',question:'请提供商品特写素材'}),requirement({requirement_id:'waived',decision:'waived'})])} disabled={false} action={action}/>);
    expect(screen.getAllByRole('textbox')).toHaveLength(2);
    fireEvent.change(screen.getAllByLabelText('对此要求的回答')[0],{target:{value:'优先缩短'}});
    fireEvent.click(screen.getAllByRole('button',{name:'保存回答并重新规划'})[0]);
    await waitFor(()=>expect(videoRefinementApi.autoPreview).toHaveBeenCalledTimes(1));
    expect(videoRefinementApi.requirements).toHaveBeenCalledWith(1,{revision:2,decisions:[{requirement_id:'shorten',decision:'answered',answer:'优先缩短'}]});
  });
});
