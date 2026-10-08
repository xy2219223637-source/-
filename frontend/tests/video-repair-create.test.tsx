import {cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {MemoryRouter,Route,Routes} from 'react-router-dom';
import {afterEach,describe,expect,it,vi} from 'vitest';
import VideoRepairCreate from '../src/pages/VideoRepairCreate';
import {videoRepairApi} from '../src/api/client';
import {materialLibraryApi} from '../src/api/materialLibrary';

vi.mock('../src/api/client',async()=>{
  const actual=await vi.importActual<typeof import('../src/api/client')>('../src/api/client');
  return {...actual,videoRepairApi:{...actual.videoRepairApi,uploadLibrarySource:vi.fn(),create:vi.fn()}};
});
vi.mock('../src/api/materialLibrary',()=>({materialLibraryApi:{products:vi.fn()}}));

afterEach(()=>{cleanup();vi.clearAllMocks()});

describe('新建审核失败修改',()=>{
  it('选择素材库商品、上传本地视频，并只提交问题描述与建议',async()=>{
    vi.mocked(materialLibraryApi.products).mockResolvedValue([{id:7,introduction:'测试商品',image_url:'/cover.png',created_at:'',updated_at:''}]);
    vi.mocked(videoRepairApi.uploadLibrarySource).mockResolvedValue({id:31,product_id:7,filename:'v1.mp4',duration_seconds:12});
    vi.mocked(videoRepairApi.create).mockResolvedValue({id:42} as Awaited<ReturnType<typeof videoRepairApi.create>>);
    render(<MemoryRouter initialEntries={['/video-repair/new?material_library_product_id=7']}><Routes><Route path="/video-repair/new" element={<VideoRepairCreate/>}/><Route path="/video-repair/:id" element={<p>任务页</p>}/></Routes></MemoryRouter>);
    await waitFor(()=>expect((screen.getByRole('combobox',{name:/素材库商品/}) as HTMLSelectElement).value).toBe('7'));
    expect(screen.queryByText('期望修改结果（可选）')).toBeNull();
    expect(screen.queryByText('问题时间段（可选）')).toBeNull();
    const file=new File(['video'],'v1.mp4',{type:'video/mp4'});
    fireEvent.change(screen.getByLabelText('本地原视频 *'),{target:{files:[file]}});
    fireEvent.change(screen.getByLabelText('问题描述'),{target:{value:'6 到 10 秒画面不准确'}});
    fireEvent.change(screen.getByLabelText('建议（可选，每行一条）'),{target:{value:'替换画面\n保留口播'}});
    fireEvent.click(screen.getByRole('button',{name:'创建并分析'}));
    await screen.findByText('任务页');
    expect(videoRepairApi.uploadLibrarySource).toHaveBeenCalledWith(7,file);
    expect(videoRepairApi.create).toHaveBeenCalledWith({source_asset_id:31,material_library_product_id:7,input:{raw_reason:'6 到 10 秒画面不准确',suggestions:['替换画面','保留口播']}});
    expect(vi.mocked(videoRepairApi.uploadLibrarySource).mock.invocationCallOrder[0]).toBeLessThan(vi.mocked(videoRepairApi.create).mock.invocationCallOrder[0]);
  });
});
