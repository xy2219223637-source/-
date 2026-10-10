import {cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {MemoryRouter,Route,Routes} from 'react-router-dom';
import {afterEach,beforeEach,describe,expect,it,vi} from 'vitest';
import VideoRepairCreate from '../src/pages/VideoRepairCreate';
import {ecommerceProductApi,videoRepairApi} from '../src/api/client';

vi.mock('../src/api/client',async()=>{
  const actual=await vi.importActual<typeof import('../src/api/client')>('../src/api/client');
  return {...actual,ecommerceProductApi:{...actual.ecommerceProductApi,list:vi.fn()},videoRepairApi:{...actual.videoRepairApi,uploadSource:vi.fn(),create:vi.fn()}};
});

const originalCreate=Object.getOwnPropertyDescriptor(URL,'createObjectURL');
const originalRevoke=Object.getOwnPropertyDescriptor(URL,'revokeObjectURL');
const createObjectURL=vi.fn((file:File)=>`blob:preview-${file.name}`);
const revokeObjectURL=vi.fn();
beforeEach(()=>{
  Object.defineProperty(URL,'createObjectURL',{configurable:true,value:createObjectURL});
  Object.defineProperty(URL,'revokeObjectURL',{configurable:true,value:revokeObjectURL});
});
afterEach(()=>{
  cleanup();vi.clearAllMocks();
  if(originalCreate)Object.defineProperty(URL,'createObjectURL',originalCreate);else delete (URL as Partial<typeof URL>).createObjectURL;
  if(originalRevoke)Object.defineProperty(URL,'revokeObjectURL',originalRevoke);else delete (URL as Partial<typeof URL>).revokeObjectURL;
});

describe('新建审核失败修改',()=>{
  it('手动上传原视频，创建任务并指定电商成品归属',async()=>{
    vi.mocked(ecommerceProductApi.list).mockResolvedValue([{id:7,name:'测试商品'}] as Awaited<ReturnType<typeof ecommerceProductApi.list>>);
    vi.mocked(videoRepairApi.uploadSource).mockResolvedValue({id:31,product_id:7,filename:'v1.mp4',duration_seconds:12});
    vi.mocked(videoRepairApi.create).mockResolvedValue({id:42} as Awaited<ReturnType<typeof videoRepairApi.create>>);
    render(<MemoryRouter initialEntries={['/video-repair/new']}><Routes><Route path="/video-repair/new" element={<VideoRepairCreate/>}/><Route path="/video-repair/:id" element={<p>任务页</p>}/></Routes></MemoryRouter>);
    await waitFor(()=>expect(screen.getByRole('option',{name:'测试商品'})).toBeTruthy());
    fireEvent.change(screen.getByRole('combobox',{name:/新成片所属电商商品/}),{target:{value:'7'}});
    expect(screen.queryByText('期望修改结果（可选）')).toBeNull();
    expect(screen.queryByText('问题时间段（可选）')).toBeNull();
    const file=new File(['video'],'v1.mp4',{type:'video/mp4'});
    fireEvent.change(screen.getByLabelText('本地原视频 *'),{target:{files:[file]}});
    expect((screen.getByLabelText('本地原视频预览') as HTMLVideoElement).src).toContain('blob:preview-v1.mp4');
    fireEvent.change(screen.getByRole('textbox',{name:/审核失败原因/}),{target:{value:'6 到 10 秒画面不准确'}});
    fireEvent.change(screen.getByLabelText('修改建议（可选，每行一条）'),{target:{value:'替换画面\n保留口播'}});
    fireEvent.click(screen.getByRole('button',{name:'创建并分析'}));
    await screen.findByText('任务页');
    expect(videoRepairApi.uploadSource).toHaveBeenCalledWith(7,file);
    expect(videoRepairApi.create).toHaveBeenCalledWith({source_asset_id:31,ecommerce_product_id:7,input:{raw_reason:'6 到 10 秒画面不准确',suggestions:['替换画面','保留口播']}});
    expect(vi.mocked(videoRepairApi.uploadSource).mock.invocationCallOrder[0]).toBeLessThan(vi.mocked(videoRepairApi.create).mock.invocationCallOrder[0]);
  });

  it('更换视频时更新预览，并释放旧文件的临时地址',async()=>{
    vi.mocked(ecommerceProductApi.list).mockResolvedValue([]);
    const view=render(<MemoryRouter><VideoRepairCreate/></MemoryRouter>);
    const input=screen.getByLabelText('本地原视频 *');
    fireEvent.change(input,{target:{files:[new File(['one'],'first.mp4',{type:'video/mp4'})]}});
    expect((screen.getByLabelText('本地原视频预览') as HTMLVideoElement).src).toContain('blob:preview-first.mp4');
    fireEvent.change(input,{target:{files:[new File(['two'],'second.mp4',{type:'video/mp4'})]}});
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:preview-first.mp4');
    expect((screen.getByLabelText('本地原视频预览') as HTMLVideoElement).src).toContain('blob:preview-second.mp4');
    view.unmount();
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:preview-second.mp4');
  });
});
